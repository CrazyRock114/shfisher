"""出钓指数评分引擎：100 分制 + 硬门槛。

规则来源：docs/03-天气监控调研.md 第二节（钓圈共识经验，无官方公开算法；
市面"钓鱼指数"均为黑盒，本实现刻意保持规则透明、逐项可解释）。
所有阈值集中在常量区，后续可用钓鱼日志回填校准。
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

# ---------- 阈值常量（调研得出，可校准） ----------
PRESSURE_GOOD_LOW = 990.0   # 990–1005 hPa 满分区
PRESSURE_GOOD_HIGH = 1005.0
PRESSURE_OK_HIGH = 1020.0   # 1005–1020 得 15，>1020 得 12
TREND_STRONG = 4.0          # 24h 气压变化 ≥ +4 hPa 满分
TREND_MILD = 2.0

WIND_GATE_MS = 10.8         # 蒲福 6 级下限：硬门槛
GUST_GATE_MS = 13.9         # 蒲福 7 级下限：硬门槛
GUST_WARN_MS = 10.8         # 阵风达 6 级：风力分打折

RAIN_GATE_MM = 8.0          # 小时雨强 ≥ 8mm：硬门槛
DRIZZLE_MM = 0.5            # ≤0.5mm/h 视为毛毛雨
AFTER_RAIN_MM = 1.0         # 过去 24h 累计雨量 >1mm 视为"雨后"
AFTER_RAIN_HOURS = 24

WEIGHTS = {"trend": 25, "abs_p": 20, "wind": 20, "season": 15, "rain": 10, "comfort": 10}

# 蒲福风级下限（m/s），索引即风级
BEAUFORT_MIN = [0.0, 0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7]

# 8 方位：deg + 22.5 后每 45° 一档
_DIR_NAMES = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
_DIR_BONUS = {"东北": 3, "东南": 3, "南": 3, "西南": -5}


def wind_level(speed_ms: Optional[float]) -> int:
    if speed_ms is None:
        return 0
    level = 0
    for i, lo in enumerate(BEAUFORT_MIN):
        if speed_ms >= lo:
            level = i
    return level


def wind_dir_text(deg: Optional[float]) -> str:
    if deg is None:
        return "—"
    return _DIR_NAMES[int(((deg + 22.5) % 360) // 45)]


def verdict_text(score: float, gated: bool = False) -> str:
    if gated:
        return "不可出钓"
    if score >= 75:
        return "优秀出钓时段"
    if score >= 55:
        return "可以出钓"
    if score >= 35:
        return "勉强（有更好选择）"
    return "建议改天"


# ---------- 各分项 ----------

def _trend_score(delta: float) -> tuple[int, str]:
    if delta >= TREND_STRONG:
        return 25, f"气压24h回升 {delta:+.1f} hPa（强势回升）"
    if delta >= TREND_MILD:
        return 18, f"气压24h回升 {delta:+.1f} hPa"
    if delta > -TREND_MILD:
        return 12, f"气压24h基本持平 {delta:+.1f} hPa"
    if delta > -TREND_STRONG:
        return 6, f"气压24h下降 {delta:+.1f} hPa（鱼口转差）"
    return 0, f"气压24h大跌 {delta:+.1f} hPa（不建议出钓）"


def _abs_pressure_score(p: float) -> tuple[int, str]:
    if PRESSURE_GOOD_LOW <= p <= PRESSURE_GOOD_HIGH:
        return 20, f"气压 {p:.0f} hPa 处于适钓区间"
    if PRESSURE_GOOD_HIGH < p <= PRESSURE_OK_HIGH:
        return 15, f"气压 {p:.0f} hPa 略偏高"
    if p < PRESSURE_GOOD_LOW:
        return 3, f"气压 {p:.0f} hPa 过低（鱼易浮头，可试钓浮）"
    return 12, f"气压 {p:.0f} hPa 偏高（鱼口一般）"


def _wind_score(lvl: int, gust_lvl: int, temp: float, wdir: Optional[float]) -> tuple[int, str]:
    if 2 <= lvl <= 4:
        s, note = 20, "2–4 级好风（增氧又好看漂）"
    elif lvl == 1:
        s, note = 9, "1 级风偏小"
    elif lvl == 5:
        s, note = 8, "5 级风偏大，看漂吃力"
    elif lvl == 0:
        if temp > 30:
            s, note = 5, "无风且高温（闷热缺氧）"
        else:
            s, note = 9, "无风（水面平静）"
    else:
        s, note = 0, f"{lvl} 级大风"
    if gust_lvl >= 6:
        s = min(s, 8)
        note += f"，阵风 {gust_lvl} 级"
    name = wind_dir_text(wdir)
    bonus = _DIR_BONUS.get(name)
    if bonus:
        sign = "加成" if bonus > 0 else "减分"
        note += f"，{name}风{sign}"
        s += bonus
    return max(0, min(20, s)), f"{name}风 {lvl} 级 · {note}"


def _rain_score(i: int, precs: list[Optional[float]]) -> tuple[int, str]:
    prec = precs[i] or 0.0
    if prec > DRIZZLE_MM:
        return 2, f"正在降雨 {prec:.1f} mm/h（雨中难钓）"
    if prec > 0.05:
        return 8, f"毛毛雨 {prec:.1f} mm/h（可钓）"
    past = sum(p or 0.0 for p in precs[max(0, i - AFTER_RAIN_HOURS): i])
    if past > AFTER_RAIN_MM:
        hours_ago = None
        for k in range(i - 1, max(0, i - AFTER_RAIN_HOURS) - 1, -1):
            if (precs[k] or 0.0) > 0.1:
                hours_ago = i - k
                break
        extra = f"（雨后约 {hours_ago} 小时）" if hours_ago else ""
        return 10, f"雨后溶氧充足{extra}"
    return 6, "无雨稳定天"


def _season_score(t: datetime, temp: float) -> tuple[int, str]:
    m, hr = t.month, t.hour
    if m in (6, 7, 8):
        if 4 <= hr <= 9 or 18 <= hr <= 21:
            return 15, "夏季黄金时段（早晚口）"
        if 11 <= hr <= 15:
            return 0, "夏季午间高温，鱼口差"
        return 7, "夏季过渡时段"
    if m in (12, 1, 2):
        if 9 <= hr <= 15:
            return 15, "冬季午间窗口（钓深钓暖）"
        return 3, "冬季早晚低温，鱼口弱"
    if 15 <= temp <= 28:
        return 15, "春秋适温，全天可钓"
    if temp <= 5 or temp >= 35:
        return 7, f"春秋但气温 {temp:.0f}℃ 过极端，开口差"
    return 12, "春秋全天可钓"


def _comfort_score(
    t: datetime,
    day_temps: dict[date, list[float]],
    rh: Optional[float],
    wind_lvl: int,
) -> tuple[int, str]:
    temps = day_temps.get(t.date(), [])
    diff = (max(temps) - min(temps)) if len(temps) >= 2 else 0.0
    s = 10.0
    notes = []
    if diff > 5:
        s -= 3 * math.ceil((diff - 5) / 3)
        notes.append(f"昼夜温差 {diff:.0f}℃ 偏大")
    else:
        notes.append(f"昼夜温差 {diff:.0f}℃ 稳定")
    if rh is not None and rh >= 85 and wind_lvl < 2:
        s -= 3
        notes.append("高湿无风（闷热修正）")
    return max(0, min(10, s)), "；".join(notes)


# ---------- 主流程 ----------

def _score_hour(
    i: int,
    times: list[datetime],
    temps: list[Optional[float]],
    rhs: list[Optional[float]],
    precs: list[Optional[float]],
    winds: list[Optional[float]],
    gusts: list[Optional[float]],
    dirs_: list[Optional[float]],
    press: list[Optional[float]],
    day_temps: dict[date, list[float]],
) -> dict[str, Any]:
    t = times[i]
    temp = temps[i] or 0.0
    rh, prec = rhs[i], precs[i] or 0.0
    wind, gust, wdir, p = winds[i], gusts[i], dirs_[i], press[i] or 1000.0

    parts: dict[str, float] = {}
    reasons: list[str] = []
    gates: list[str] = []

    # 1) 气压趋势（相对 24h 前）
    if i - 24 >= 0 and press[i - 24] is not None:
        parts["trend"], r = _trend_score(p - press[i - 24])
        reasons.append(r)
    else:
        parts["trend"] = 12
        reasons.append("气压趋势数据不足，按中性计")

    # 2) 气压绝对值
    parts["abs_p"], r = _abs_pressure_score(p)
    reasons.append(r)

    # 3) 风（含硬门槛）
    lvl = wind_level(wind)
    gust_lvl = wind_level(gust)
    if wind is not None and wind >= WIND_GATE_MS:
        gates.append(f"风力 {lvl} 级（≥6 级），硬性不可出钓")
        parts["wind"] = 0
    elif gust is not None and gust >= GUST_GATE_MS:
        gates.append(f"阵风 {gust_lvl} 级（≥7 级），硬性不可出钓")
        parts["wind"] = 0
    else:
        parts["wind"], r = _wind_score(lvl, gust_lvl, temp, wdir)
        reasons.append(r)

    # 4) 降水（含硬门槛）
    if prec >= RAIN_GATE_MM:
        gates.append(f"小时雨强 {prec:.0f} mm（强降雨），硬性不可出钓")
        parts["rain"] = 0
    else:
        parts["rain"], r = _rain_score(i, precs)
        reasons.append(r)

    # 5) 季节时段
    parts["season"], r = _season_score(t, temp)
    reasons.append(r)

    # 6) 温差与湿度修正
    parts["comfort"], r = _comfort_score(t, day_temps, rh, lvl)
    reasons.append(r)

    gated = bool(gates)
    score = 0 if gated else max(0, min(100, round(sum(parts.values()))))
    return {
        "time": t.isoformat(timespec="minutes"),
        "date": t.date().isoformat(),
        "hh": t.hour,
        "score": score,
        "gate": gated,
        "gate_reasons": gates,
        "parts": {k: int(v) for k, v in parts.items()},
        "reasons": reasons,
        "verdict": verdict_text(score, gated),
        "temp": None if temps[i] is None else round(temp, 1),
        "humidity": rh,
        "precip": prec,
        "pressure": p,
        "pressure_trend_24h": None if i - 24 < 0 or press[i - 24] is None else round(p - press[i - 24], 1),
        "wind_speed": wind,
        "wind_level": lvl,
        "wind_gust": gust,
        "wind_dir": wind_dir_text(wdir),
    }


def _best_windows(hourly: list[dict[str, Any]], top: int = 3) -> list[dict[str, Any]]:
    runs: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for rec in hourly:
        if (not rec["gate"]) and rec["score"] >= 55:
            cur.append(rec)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    windows = []
    for run in runs:
        avg = sum(r["score"] for r in run) / len(run)
        windows.append(
            {
                "start": run[0]["time"],
                "end": run[-1]["time"],
                "hours": len(run),
                "avg_score": round(avg),
                "verdict": "优秀窗口" if avg >= 75 else "可用窗口",
            }
        )
    windows.sort(key=lambda w: -w["avg_score"])
    return windows[:top]


def compute_scores(data: dict[str, Any], now: datetime, max_hours: int = 48) -> dict[str, Any]:
    """把 Open-Meteo 逐小时响应转成评分结果。

    now：带时区的当前时间。Open-Meteo 返回的是不带时区标记的当地时间
    （本服务固定请求 Asia/Shanghai），因此统一转成上海墙上时间后再比较。
    展示窗口从当前小时起 max_hours 小时。
    """
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    h = data["hourly"]
    times = [datetime.fromisoformat(t) for t in h["time"]]
    temps = h.get("temperature_2m", [])
    rhs = h.get("relative_humidity_2m", [])
    precs = h.get("precipitation", [])
    winds = h.get("wind_speed_10m", [])
    gusts = h.get("wind_gusts_10m", [])
    dirs_ = h.get("wind_direction_10m", [])
    press = h.get("pressure_msl", [])
    n = len(times)
    if not (len(temps) == len(rhs) == len(precs) == len(winds) == len(gusts) == len(dirs_) == len(press) == n) or n == 0:
        raise ValueError("Open-Meteo 响应字段缺失或长度不一致")

    idx_now = 0
    for i, t in enumerate(times):
        if t <= now:
            idx_now = i
        else:
            break

    day_temps: dict[date, list[float]] = {}
    for t, v in zip(times, temps):
        if v is not None:
            day_temps.setdefault(t.date(), []).append(v)

    hourly = [
        _score_hour(i, times, temps, rhs, precs, winds, gusts, dirs_, press, day_temps)
        for i in range(idx_now, min(n, idx_now + max_hours + 1))
    ]

    # 按日汇总
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in hourly:
        groups.setdefault(rec["date"], []).append(rec)
    days = []
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for d, recs in groups.items():
        best = max(recs, key=lambda r: r["score"])
        d_date = date.fromisoformat(d)
        if d_date == now.date():
            day_label = "今天"
        elif d_date == now.date() + timedelta(days=1):
            day_label = "明天"
        elif d_date == now.date() + timedelta(days=2):
            day_label = "后天"
        else:
            day_label = d_date.strftime("%m-%d") + weekday_names[d_date.weekday()]
        days.append(
            {
                "date": d,
                "label": day_label,
                "max_score": best["score"],
                "best_time": f"{best['hh']:02d}:00",
                "gated_hours": sum(1 for r in recs if r["gate"]),
                "verdict": best["verdict"],
            }
        )

    windows = _best_windows(hourly)
    current = hourly[0]
    summary = (
        f"当前 {current['verdict']}（{current['score']} 分）"
        if not current["gate"]
        else f"当前不可出钓：{current['gate_reasons'][0]}"
    )
    if not windows:
        fallback = sorted((r for r in hourly if not r["gate"]), key=lambda r: -r["score"])[:3]
        summary += "；未来 48 小时没有 ≥55 分的连续窗口，建议改天"
    else:
        w = windows[0]
        summary += f"；最佳窗口 {w['start'][5:16]}–{w['end'][11:16]}（均分 {w['avg_score']}）"

    return {
        "hourly": hourly,
        "days": days,
        "best_windows": windows,
        "summary": summary,
        "rule_weights": WEIGHTS,
    }
