"""潮汐/水文模块。

数据源（均为官方免费公开页面，2026-10 探测核实）：
1. 上海海事局"潮汐查询"——服务端渲染 HTML，GET ?PlaceId={站号}&TideDate={YYYY-MM-DD}
   直接返回当日高低潮时刻/潮高与 24 小时逐时潮高表。无反爬、无鉴权。
   站点表来自页面内嵌 weui.picker 配置。
2. 上海市水务局"便民服务"JSON 接口——实时水位(type=SSSW)、预报潮位(type=YJSW)、
   预警(getCurrentXy)。

钓法语义（docs/02-水文监控调研.md）：上海感潮河段每日两涨两落；涨潮好钓、
平潮前后最难钓、转流初期是黄金窗口。本模块把潮汐表转成"当前所处阶段 +
下次转流窗口"的钓鱼语义。
"""

from __future__ import annotations

import re
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

MSA_TIDE_URL = "https://www.sh.msa.gov.cn/shhsfb/information-aim-navigation/tide-search"
SWJ_LIST_URL = "https://swgxh.swj.sh.gov.cn/swj/swj/businessConvenientService/getList"

# 站点表与页面内嵌 weui.picker 一致（2026-10 抓取）
STATIONS: dict[str, int] = {
    "吴淞": 1,
    "高桥": 2,
    "黄浦公园": 3,
    "吴泾": 4,
    "青龙港": 5,
    "白茆": 6,
    "天生港": 7,
    "江阴": 8,
    "石洞口": 9,
    "南门港": 10,
    "长兴": 11,
    "横沙": 12,
    "中浚": 13,
    "北槽中": 14,
    "牛皮礁": 15,
    "佘山": 16,
    "鸡骨礁": 17,
    "南槽东": 18,
    "大戢山": 19,
    "绿华": 20,
    "芦潮港": 21,
}
DEFAULT_STATION = "吴淞"

# 黄浦江沿线钓点更靠近哪个潮汐站（按纬度粗分：上游靠吴泾/黄浦公园，下游靠吴淞）
TIDE_TTL_SECONDS = 6 * 3600       # 潮汐表当天内基本不变
WATERLEVEL_TTL_SECONDS = 600      # 实时水位 10 分钟缓存

_cache: dict[tuple, tuple[float, Any]] = {}

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fisherman-app/0.2"}

# 阶段定义：(标签, 质量1-5, 建议)
PHASE_ADVICE = {
    "涨潮": (4, "潮水顶托、溶氧升高，鱼随潮靠岸觅食——总体好钓，可用长竿钓近岸"),
    "落潮": (2, "落潮中前段尚可；中后段水位下降鱼退深水，口渐差"),
    "平潮": (1, "水体近乎静止、溶氧低，最难咬钩——适合休整补窝，等转流"),
    "转流初期": (5, "急流转缓、饵料鱼被潮流搬运，掠食鱼最活跃——黄金窗口，务必抓住"),
}

# 业务时区：潮汐/水位判定一律按上海墙上时间，与部署机器时区解耦
from zoneinfo import ZoneInfo as _ZoneInfo

TZ_SH = _ZoneInfo("Asia/Shanghai")


# ---------- 抓取与解析 ----------

async def fetch_tide_html(station_id: int, query_date: str, client: httpx.AsyncClient) -> str:
    resp = await client.get(
        MSA_TIDE_URL, params={"PlaceId": station_id, "TideDate": query_date}, headers=_UA
    )
    resp.raise_for_status()
    return resp.text


def parse_tide_html(html: str) -> dict[str, Any]:
    """解析海事局潮汐页（服务端渲染）。结构见 tests/fixtures/msa_tide.html。"""

    def _row_values(anchor: str, value_pattern: str) -> list[str]:
        m = re.search(r"<td[^>]*>" + re.escape(anchor) + r"</td>(.*?)</tr>", html, re.S)
        if not m:
            return []
        return re.findall(value_pattern, m.group(1))

    title_m = re.search(r"<b>([^<\s]+)\s+(\d{4}-\d{2}-\d{2})\s*潮汐表</b>", html)
    station = title_m.group(1) if title_m else None
    query_date = title_m.group(2) if title_m else None

    stand_times = _row_values("潮时(Hrs)", r"<td>(\d{1,2}:\d{2})</td>")
    stand_heights = _row_values("潮高(cm)", r"<td>(\d+)</td>")
    hour_rows = re.findall(r"<td[^>]*>潮时</td>(.*?)</tr>", html, re.S)
    height_rows = re.findall(r"<td[^>]*>潮高</td>(.*?)</tr>", html, re.S)

    hours: list[int] = []
    for row in hour_rows:
        hours.extend(int(v) for v in re.findall(r"<td>(\d+)</td>", row))
    heights: list[int] = []
    for row in height_rows:
        heights.extend(int(v) for v in re.findall(r"<td>(\d+)</td>", row))

    if not stand_times or len(stand_times) != len(stand_heights) or len(heights) != 24:
        raise ValueError("潮汐页解析失败：表格结构不符合预期（页面改版或返回异常）")

    stands = []
    for t, h in zip(stand_times, stand_heights):
        stands.append({"time": t, "height": int(h)})

    # 高低潮交替：以相邻两个潮位比较定首个性质
    if len(stands) >= 2:
        first_is_high = stands[0]["height"] > stands[1]["height"]
    else:
        first_is_high = stands[0]["height"] >= 200  # 兜底：上海沿海高潮一般显著高于低潮
    for i, s in enumerate(stands):
        s["type"] = ("high" if (i % 2 == 0) == first_is_high else "low")

    return {
        "station": station,
        "date": query_date,
        "stands": stands,
        "hourly": heights,
        "hours": hours,
    }


async def get_tide(station: str = DEFAULT_STATION, days: int = 2) -> dict[str, Any]:
    """取 station 起始日的 days 天潮汐并计算钓鱼阶段。"""
    station_id = STATIONS.get(station)
    if not station_id:
        raise ValueError(f"未知潮汐站：{station}，可选：{'、'.join(STATIONS)}")
    today = datetime.now().date()
    result_days = []
    parsed_days = []
    async with httpx.AsyncClient(timeout=15) as client:
        for offset in range(days):
            d = today + timedelta(days=offset)
            key = ("tide", station_id, d.isoformat())
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < TIDE_TTL_SECONDS:
                parsed = hit[1]
            else:
                html = await fetch_tide_html(station_id, d.isoformat(), client)
                parsed = parse_tide_html(html)
                _cache[key] = (time.monotonic(), parsed)
            parsed_days.append(parsed)
            result_days.append(build_phases(parsed))

    current, next_turn = summarize_now(result_days)
    return {
        "station": station,
        "stations": list(STATIONS.keys()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": result_days,
        "current": current,
        "next_turn": next_turn,
        "advice_summary": _summary(current, next_turn),
    }


# ---------- 钓鱼阶段计算 ----------

def build_phases(parsed: dict[str, Any]) -> dict[str, Any]:
    """把当日潮汐表转成 0-24h 的钓鱼阶段时间轴（分钟级网格，确定性无重叠）。

    阶段语义（docs/02）：
    - 平潮：高潮/低潮时刻前 40 分钟 ~ 后 30 分钟，最难钓；
    - 转流初期：平潮结束后 2.5 小时，黄金窗口；
    - 涨潮：低潮向高潮推进段，好钓；
    - 落潮：高潮向低潮退去段，中前段尚可、后段差。
    """
    N_MIN = 24 * 60
    SLACK_BEFORE, SLACK_AFTER = 40, 30  # 分钟
    TURN_LEN = 150  # 平潮结束后 2.5 小时

    def hm_min(hm: str) -> int:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)

    stands_min = [(hm_min(s["time"]), s["type"]) for s in parsed["stands"]]
    labels: list[Optional[str]] = [None] * N_MIN

    # 基础层：相邻 stand 之间的涨/落潮；首尾段由相邻 stand 性质推断
    for m in range(N_MIN):
        nxt = next((t for t, _ in stands_min if t > m), None)
        if nxt is None:
            last_type = stands_min[-1][1] if stands_min else "high"
            labels[m] = "落潮" if last_type == "high" else "涨潮"
        else:
            labels[m] = "涨潮" if _type_at(stands_min, nxt) == "high" else "落潮"

    # 覆盖层：转流初期（先写），平潮（最高优先级，最后写）
    for t, _ in stands_min:
        for m in range(min(t + SLACK_AFTER, N_MIN - 1), min(t + SLACK_AFTER + TURN_LEN, N_MIN)):
            labels[m] = "转流初期"
    for t, _ in stands_min:
        for m in range(max(t - SLACK_BEFORE, 0), min(t + SLACK_AFTER, N_MIN)):
            labels[m] = "平潮"

    # 压缩成连续区段
    def fmt(m: int) -> str:
        if m >= 1440:
            return "24:00"
        return (datetime.combine(date_cls.fromisoformat(parsed["date"]), datetime.min.time()) + timedelta(minutes=m)).strftime("%H:%M")

    phases = []
    seg_start = 0
    for m in range(1, N_MIN + 1):
        if m == N_MIN or labels[m] != labels[seg_start]:
            label = labels[seg_start] or "落潮"
            quality, advice = PHASE_ADVICE[label]
            phases.append(
                {
                    "start": fmt(seg_start),
                    "end": fmt(m),
                    "start_min": seg_start,
                    "end_min": m,
                    "label": label,
                    "quality": quality,
                    "advice": advice,
                }
            )
            seg_start = m
    return {
        "date": parsed["date"],
        "station": parsed["station"],
        "hourly": parsed["hourly"],
        "stands": parsed["stands"],
        "phases": phases,
    }


def _type_at(stands_min: list[tuple[int, str]], target: int) -> str:
    """返回时间为 target 的 stand 的类型。"""
    for t, typ in stands_min:
        if t == target:
            return typ
    return "high"


def _fmt_hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def summarize_now(days: list[dict[str, Any]], now: Optional[datetime] = None) -> tuple[Optional[dict], Optional[dict]]:
    """返回当前所处阶段与下一次转流初期窗口（跨天查找）。

    now 默认取 Asia/Shanghai 当前时间（**不是**机器本地时区——个人设备
    带出国/改时区时潮汐判定仍按上海墙上时间）。用 start_min/end_min（当日
    0 点起的分钟数）还原绝对时间，避免 "24:00" 被解析成次日 00:00 的歧义。
    """
    if now is None:
        now = datetime.now(TZ_SH)
    if now.tzinfo is not None:
        now = now.astimezone(TZ_SH).replace(tzinfo=None)
    current = None
    next_turn = None
    for day in days:
        d = date_cls.fromisoformat(day["date"])
        day_start = datetime.combine(d, datetime.min.time())
        for ph in day["phases"]:
            start = day_start + timedelta(minutes=ph["start_min"])
            end = day_start + timedelta(minutes=ph["end_min"])
            if start <= now < end and current is None:
                current = {k: ph[k] for k in ("label", "quality", "advice")}
                current["until"] = ph["end"]
                current["date"] = day["date"]
            if ph["label"] == "转流初期" and end > now and next_turn is None:
                next_turn = {"start": ph["start"], "end": ph["end"], "date": day["date"]}
    return current, next_turn


def _summary(current: Optional[dict], next_turn: Optional[dict]) -> str:
    parts = []
    if current:
        parts.append(f"当前 {current['label']}（{current['until']} 前），{current['advice']}")
    if next_turn:
        when = "今天" if next_turn["date"] == datetime.now().date().isoformat() else next_turn["date"]
        parts.append(f"下一个转流窗口：{when} {next_turn['start']}–{next_turn['end']}")
    return "；".join(parts) if parts else "潮汐数据不足"


# ---------- 水务局实时/预报水位 ----------

async def get_water_level() -> dict[str, Any]:
    key = ("waterlevel",)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < WATERLEVEL_TTL_SECONDS:
        return hit[1]
    async with httpx.AsyncClient(timeout=15) as client:
        rt = await client.get(SWJ_LIST_URL, params={"type": "SSSW"}, headers=_UA)
        fc = await client.get(SWJ_LIST_URL, params={"type": "YJSW"}, headers=_UA)
        rt.raise_for_status()
        fc.raise_for_status()
        rt_data = rt.json().get("data", [])
        fc_data = fc.json().get("data", [])

    realtime = [
        {
            "station": item.get("STATIONNAME"),
            "time": item.get("DATETIME"),
            "level": item.get("OUTWATER"),
            "river": item.get("HELIU"),
            "region": item.get("QUYU"),
        }
        for item in rt_data[:6]
    ]
    forecast: dict[str, list] = {}
    for item in fc_data:
        forecast.setdefault(item.get("STATIONNAME", ""), []).append(
            {"time": item.get("DATETIME"), "height": item.get("YBCW")}
        )
    result = {
        "realtime": realtime,
        "forecast": [{"station": k, "items": v} for k, v in forecast.items()],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _cache[key] = (time.monotonic(), result)
    return result
