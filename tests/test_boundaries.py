"""评分引擎规格边界矩阵。

Oracle 来源：docs/03-天气监控调研.md 第二节评分规则表（规格，非实现）。
每条断言预期均从规格推导，边界取 ±0.1 步进。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from server.scoring import _abs_pressure_score, _comfort_score, _rain_score, _season_score, _trend_score, _wind_score
from tests.test_scoring import TZ, make_payload, run

# ---------- 1. 气压趋势（规格：≥+4 满分 25；+2~+4 得 18；±2 得 12；下降扣至 0-6） ----------

@pytest.mark.parametrize(
    "delta,expected",
    [
        (4.0, 25),    # 边界：≥+4 满分
        (3.9, 18),    # 边界下探
        (2.0, 18),    # 边界：≥+2 得 18
        (1.9, 12),    # 边界下探
        (0.0, 12),    # 持平
        (-1.9, 12),   # 边界：> -2 得 12
        (-2.0, 6),    # 边界：≤-2 进入下降档
        (-3.9, 6),    # 边界：> -4 得 6
        (-4.0, 0),    # 边界：≤-4 清零
    ],
)
def test_trend_spec_boundaries(delta, expected):
    assert _trend_score(delta)[0] == expected, f"Δ={delta}"


# ---------- 2. 气压绝对值（规格：990-1005 满分 20；1005-1020 得 15；<990 得 3；>1020 得 12） ----------

@pytest.mark.parametrize(
    "p,expected",
    [
        (989.9, 3),
        (990.0, 20),   # 边界含
        (1005.0, 20),  # 边界含
        (1005.1, 15),
        (1020.0, 15),  # 边界含
        (1020.1, 12),
    ],
)
def test_abs_pressure_spec_boundaries(p, expected):
    assert _abs_pressure_score(p)[0] == expected


# ---------- 3. 风硬门槛（规格：≥6 级即风速 ≥10.8 m/s 一票否决；阵风 ≥7 级 13.9 否决） ----------

def test_wind_gate_boundary_10_8():
    # 10.79 m/s（5 级上限内）不得触发门槛；10.8 必须触发
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    ok = run(make_payload(overrides={36: {"wind_speed_10m": 10.79, "wind_gusts_10m": 4.0}}), now)
    gated = run(make_payload(overrides={36: {"wind_speed_10m": 10.8, "wind_gusts_10m": 4.0}}), now)
    assert not ok["hourly"][0]["gate"]
    assert gated["hourly"][0]["gate"]
    assert gated["hourly"][0]["score"] == 0


def test_wind_gust_gate_boundary_13_9():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    ok = run(make_payload(overrides={36: {"wind_speed_10m": 4.0, "wind_gusts_10m": 13.89}}), now)
    gated = run(make_payload(overrides={36: {"wind_speed_10m": 4.0, "wind_gusts_10m": 13.9}}), now)
    assert not ok["hourly"][0]["gate"]
    assert gated["hourly"][0]["gate"]


def test_wind_bonus_clamped_to_20():
    # 2-4 级满分 20 + 东南风加成 3 → 必须夹在 20，不得溢出到 23
    s, _ = _wind_score(3, 3, 22.0, 135)
    assert s == 20


def test_wind_bonus_cannot_go_below_zero():
    # 5 级 8 分 + 西南风 -5 → 3；0 级高温 5 分 - 5 → 夹在 0
    assert _wind_score(5, 5, 22.0, 225)[0] == 3
    assert _wind_score(0, 0, 33.0, 225)[0] == 0


# ---------- 4. 降水（规格：≥8mm/h 一票否决；毛毛雨得 8；雨中 0-3） ----------

def test_rain_gate_boundary_8mm():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    ok = run(make_payload(overrides={36: {"precipitation": 7.9}}), now)
    gated = run(make_payload(overrides={36: {"precipitation": 8.0}}), now)
    assert not ok["hourly"][0]["gate"]
    assert ok["hourly"][0]["parts"]["rain"] == 2  # 雨中
    assert gated["hourly"][0]["gate"]


@pytest.mark.parametrize(
    "mm,expected",
    [
        (0.05, 6),   # 低于毛毛雨阈值 → 按无雨路径
        (0.06, 8),   # 边界上探 → 毛毛雨
        (0.5, 8),    # 边界含 → 毛毛雨
        (0.51, 2),   # 边界上探 → 雨中
    ],
)
def test_rain_drizzle_boundaries(mm, expected):
    precs = [0.0] * 120
    precs[60] = mm
    assert _rain_score(60, precs)[0] == expected


# ---------- 5. 温差湿度修正（规格：温差 ≤5℃ 满分，每 +3℃ 扣 3；湿度 ≥85 且风 <2 级再扣 3） ----------

@pytest.mark.parametrize(
    "diff,expected",
    [
        (5.0, 10),   # 边界含
        (5.1, 7),    # 超界即扣
        (8.0, 7),
        (8.1, 4),
        (11.0, 4),
        (11.1, 1),
        (14.0, 1),
        (14.1, 0),   # 夹底
        (20.0, 0),
    ],
)
def test_comfort_diff_boundaries(diff, expected):
    from datetime import date as date_cls

    d = date_cls(2026, 10, 2)
    day_temps = {d: [10.0, 10.0 + diff]}
    s, _ = _comfort_score(datetime(2026, 10, 2, 12, 0), day_temps, 60, 3)
    assert s == expected


def test_comfort_humidity_boundary():
    from datetime import date as date_cls

    d = date_cls(2026, 10, 2)
    day_temps = {d: [20.0, 22.0]}
    base = _comfort_score(datetime(2026, 10, 2, 12, 0), day_temps, 84.9, 1)[0]
    penal = _comfort_score(datetime(2026, 10, 2, 12, 0), day_temps, 85.0, 1)[0]
    assert base == 10
    assert penal == 7  # 85 触发闷热修正


# ---------- 6. 季节时段窗口边界（规格：夏 4-9/18-21 满分、11-15 零分；冬 9-15 满分） ----------

@pytest.mark.parametrize(
    "month,hour,expected",
    [
        (6, 4, 15), (6, 9, 15),   # 夏早口边界含
        (6, 10, 7),               # 过渡（规格空白，实现自定，锁定行为防漂移）
        (6, 11, 0), (6, 15, 0),   # 夏午间零分边界含
        (6, 16, 7),
        (6, 18, 15), (6, 21, 15),  # 夏晚口边界含
        (6, 22, 7),
        (12, 8, 3), (1, 9, 15), (2, 15, 15),  # 冬窗口边界含
        (12, 16, 3),
        (5, 12, 15), (9, 12, 15),  # 春秋适温满分
    ],
)
def test_season_window_boundaries(month, hour, expected):
    t = datetime(2026, month, 15, hour, 0)
    s, _ = _season_score(t, 22.0)
    assert s == expected, f"{month}月{hour}点"


# ---------- 7. 组合性质：门槛优先、总分一致性、分项值域 ----------

def test_gate_overrides_any_score():
    # 即使其他分项全优，门槛小时必须 0 分
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    res = run(
        make_payload(
            pressure=1000.0,
            pressure_rise_per_day=8.0,
            overrides={36: {"wind_speed_10m": 15.0, "wind_gusts_10m": 18.0}},
        ),
        now,
    )
    rec = res["hourly"][0]
    assert rec["gate"] and rec["score"] == 0


def test_property_score_always_consistent():
    import random

    random.seed(42)
    now = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    for _ in range(30):
        payload = make_payload(
            temp=random.uniform(0, 38),
            rh=random.randint(30, 100),
            prec=random.choice([0.0, 0.2, 0.6, 3.0, 7.9]),
            wind=random.uniform(0, 14),
            gust=random.uniform(0, 18),
            pressure=random.uniform(985, 1030),
            overrides={36: {"precipitation": random.choice([0.0, 0.3, 1.0, 9.0])}},
        )
        res = run(payload, now)
        for h in res["hourly"]:
            assert 0 <= h["score"] <= 100
            if not h["gate"]:
                assert h["score"] == sum(h["parts"].values())
            assert h["parts"]["trend"] in (0, 6, 12, 18, 25)
            assert h["parts"]["abs_p"] in (3, 12, 15, 20)
            assert 0 <= h["parts"]["wind"] <= 20
            assert 0 <= h["parts"]["season"] <= 15
            assert h["parts"]["rain"] in (0, 2, 6, 8, 10)  # 0 仅出现在雨量硬门槛时
            assert 0 <= h["parts"]["comfort"] <= 10


def test_property_best_windows_contiguous_and_eligible():
    res = run(make_payload(pressure=998.0, pressure_rise_per_day=5.0))
    hourly = res["hourly"]
    for w in res["best_windows"]:
        # 窗口必须由连续的、≥55 分且未门槛的小时组成
        start_idx = next(i for i, h in enumerate(hourly) if h["time"] == w["start"])
        end_idx = next(i for i, h in enumerate(hourly) if h["time"] == w["end"])
        seg = hourly[start_idx : end_idx + 1]
        assert len(seg) == w["hours"]
        assert all(h["score"] >= 55 and not h["gate"] for h in seg)
        avg = round(sum(h["score"] for h in seg) / len(seg))
        assert abs(avg - w["avg_score"]) <= 1  # 四舍五入容差
