"""scoring.py 评分引擎的单元测试。

用合成的小时级数据验证：风级映射、各分项打分、硬门槛、雨后加分、总分边界。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from server.scoring import compute_scores, verdict_text, wind_level, wind_dir_text

TZ = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 10, 1, 0, 0)  # 秋季，避开夏冬时段窗口逻辑


def make_payload(
    n_hours: int = 120,
    start: datetime = START,
    temp: float = 22.0,
    rh: int = 70,
    prec: float = 0.0,
    wind: float = 3.0,
    gust: float = 4.5,
    wdir: int = 135,
    pressure: float = 1000.0,
    pressure_rise_per_day: float = 0.0,
    overrides: dict[int, dict] | None = None,
) -> dict:
    """构造 Open-Meteo 形状的合成数据。overrides: {小时索引: {字段: 值}}"""
    times, temps, rhs, precs, winds, gusts, dirs_, press = ([], [], [], [], [], [], [], [])
    for i in range(n_hours):
        t = start + timedelta(hours=i)
        times.append(t.isoformat())
        temps.append(temp)
        rhs.append(rh)
        precs.append(prec)
        winds.append(wind)
        gusts.append(gust)
        dirs_.append(wdir)
        press.append(round(pressure + pressure_rise_per_day * i / 24.0, 2))
    arrays = {
        "time": times,
        "temperature_2m": temps,
        "relative_humidity_2m": rhs,
        "precipitation": precs,
        "wind_speed_10m": winds,
        "wind_gusts_10m": gusts,
        "wind_direction_10m": dirs_,
        "pressure_msl": press,
    }
    for idx, fields in (overrides or {}).items():
        for k, v in fields.items():
            arrays[k][idx] = v
    return {"hourly": arrays}


def run(payload, now: datetime | None = None):
    now = now or datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    return compute_scores(payload, now)


# ---------- 风级与方位 ----------

@pytest.mark.parametrize(
    "ms,expected",
    [(0.0, 0), (0.3, 1), (3.3, 2), (5.4, 3), (7.9, 4), (10.7, 5), (10.8, 6), (13.9, 7)],
)
def test_wind_level(ms, expected):
    assert wind_level(ms) == expected


@pytest.mark.parametrize(
    "deg,expected",
    [(0, "北"), (45, "东北"), (90, "东"), (135, "东南"), (180, "南"), (225, "西南"), (315, "西北")],
)
def test_wind_dir_text(deg, expected):
    assert wind_dir_text(deg) == expected


# ---------- 硬门槛 ----------

def test_gate_high_wind():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    # past_days=2 起算：now 对应索引 60
    payload = make_payload(overrides={65: {"wind_speed_10m": 12.0, "wind_gusts_10m": 15.0}})
    result = run(payload, now)
    gated = [h for h in result["hourly"] if h["gate"]]
    assert len(gated) == 1
    assert gated[0]["score"] == 0
    assert "硬性不可出钓" in gated[0]["gate_reasons"][0]
    assert any("6 级" in r for r in gated[0]["gate_reasons"])


def test_gate_gust():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    payload = make_payload(overrides={66: {"wind_gusts_10m": 14.5}})
    result = run(payload, now)
    gated = [h for h in result["hourly"] if h["gate"]]
    assert len(gated) == 1 and "阵风" in gated[0]["gate_reasons"][0]


def test_gate_heavy_rain():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    payload = make_payload(overrides={67: {"precipitation": 9.0}})
    result = run(payload, now)
    gated = [h for h in result["hourly"] if h["gate"]]
    assert len(gated) == 1 and "强降雨" in gated[0]["gate_reasons"][0]


# ---------- 分项打分 ----------

def test_pressure_trend_full_score():
    # 每天稳定回升 6 hPa → 24h 变化 +6 ≥ 4 → 趋势满分 25
    payload = make_payload(pressure=996.0, pressure_rise_per_day=6.0)
    result = run(payload)
    rec = result["hourly"][0]
    assert rec["parts"]["trend"] == 25
    assert rec["pressure_trend_24h"] == pytest.approx(6.0, abs=0.2)


def test_pressure_trend_fall_low_score():
    payload = make_payload(pressure=1002.0, pressure_rise_per_day=-8.0)
    result = run(payload)
    rec = result["hourly"][0]
    assert rec["parts"]["trend"] <= 6


def test_drizzle_scores_8():
    # now=2026-10-02 12:00 相对 START(10-01 00:00) 是索引 36
    payload = make_payload(overrides={36: {"precipitation": 0.3}})
    result = run(payload)
    rec = [h for h in result["hourly"] if h["time"].endswith("T12:00")][0]
    assert rec["parts"]["rain"] == 8
    assert "毛毛雨" in rec["reasons"][3]


def test_after_rain_scores_10():
    # 当前小时(索引36)之前 10 小时每小时下 1mm，当前无雨 → 雨后
    overrides = {i: {"precipitation": 1.0} for i in range(26, 36)}
    payload = make_payload(overrides=overrides)
    result = run(payload)
    rec = [h for h in result["hourly"] if h["time"].endswith("T12:00")][0]
    assert rec["parts"]["rain"] == 10
    assert "雨后" in rec["reasons"][3]


def test_stable_no_rain_scores_6():
    result = run(make_payload())
    rec = result["hourly"][0]
    assert rec["parts"]["rain"] == 6


# ---------- 总分边界与输出结构 ----------

def test_total_score_bounds_and_structure():
    result = run(make_payload())
    for h in result["hourly"]:
        assert 0 <= h["score"] <= 100
        if not h["gate"]:
            assert h["score"] == sum(h["parts"].values())
        assert set(h["parts"].keys()) == {"trend", "abs_p", "wind", "season", "rain", "comfort"}
        assert h["verdict"] == verdict_text(h["score"], h["gate"])


def test_best_windows_and_days_present():
    result = run(make_payload(pressure=1000.0, pressure_rise_per_day=6.0))
    assert isinstance(result["best_windows"], list)
    assert len(result["days"]) >= 2
    for w in result["best_windows"]:
        assert w["avg_score"] >= 55
        assert w["hours"] >= 1
    assert "最佳窗口" in result["summary"] or "建议改天" in result["summary"]


def test_no_good_window_message():
    # 全程大风 → 全部硬门槛 → 无窗口
    result = run(make_payload(wind=15.0, gust=18.0))
    assert all(h["gate"] for h in result["hourly"])
    assert result["best_windows"] == []
    assert "建议改天" in result["summary"]
