"""潮汐阶段算法：性质断言 + 边界（合成 stands，绕开 HTML 解析）。

Oracle 来源：docs/02-水文监控调研.md 钓法语义 + tide.py 模块文档
（平潮=stand 前 40min~后 30min；转流初期=平潮后 2.5h；涨潮好钓/落潮次之）。
"""

from __future__ import annotations

import pytest

from server.tide import build_phases, summarize_now


def make_parsed(stands, date="2026-10-02"):
    """stands: [(HH:MM, 'high'|'low', 高度cm)]"""
    return {
        "station": "吴淞",
        "date": date,
        "stands": [{"time": t, "height": h, "type": typ} for t, typ, h in stands],
        "hourly": [100] * 24,
        "hours": list(range(24)),
    }


def phase_minutes(day):
    return [(p["start_min"], p["end_min"], p["label"]) for p in day["phases"]]


# ---------- 性质最小集 ----------

@pytest.mark.parametrize(
    "stands",
    [
        [("03:18", "high", 337), ("10:50", "low", 128), ("15:47", "high", 390)],
        [("00:09", "low", 146), ("04:07", "high", 299), ("11:14", "low", 145), ("16:41", "high", 361)],
        [("06:00", "high", 300), ("18:30", "low", 120)],  # 全日潮（2 stand）
        [("23:50", "high", 350)],                          # 深夜 stand（跨午夜截断）
        [("00:05", "low", 100)],                           # 凌晨 stand
    ],
)
def test_property_seamless_coverage_no_gaps(stands):
    day = build_phases(make_parsed(stands))
    mins = phase_minutes(day)
    assert mins[0][0] == 0
    assert mins[-1][1] == 1440
    for (_, e1, _), (s2, _, _) in zip(mins, mins[1:]):
        assert e1 == s2  # 无缝无重叠
    for s, e, label in mins:
        assert e > s
        assert label in ("涨潮", "落潮", "平潮", "转流初期")


@pytest.mark.parametrize(
    "stands",
    [
        [("03:18", "high", 337), ("10:50", "low", 128), ("15:47", "high", 390)],
        [("00:09", "low", 146), ("04:07", "high", 299), ("11:14", "low", 145), ("16:41", "high", 361)],
    ],
)
def test_property_every_stand_has_slack_and_turn(stands):
    day = build_phases(make_parsed(stands))
    mins = phase_minutes(day)
    for t, _typ, _h in stands:
        hh, mm = map(int, t.split(":"))
        center = hh * 60 + mm
        slack = [(s, e) for s, e, l in mins if l == "平潮" and s <= center < e]
        assert slack, f"stand {t} 没有平潮覆盖"
        # 平潮在日内至少 30 分钟（前 40 或后 30 至少一段完整落在本日）
        assert any(e - s >= 30 for s, e in slack)


def test_slack_and_turn_exact_lengths_mid_day():
    day = build_phases(make_parsed([("12:00", "high", 300), ("23:00", "low", 120)]))
    slack = [p for p in day["phases"] if p["label"] == "平潮" and p["start_min"] == 11 * 60 + 20]
    turn = [p for p in day["phases"] if p["label"] == "转流初期"]
    assert slack and slack[0]["end_min"] - slack[0]["start_min"] == 70  # 40+30
    assert all(p["end_min"] - p["start_min"] == 150 for p in turn if p["end_min"] < 1440)


def test_flood_label_before_first_high():
    # 首个 stand 为 high → 00:00 起是涨潮；为 low → 落潮
    d1 = build_phases(make_parsed([("08:00", "high", 300), ("20:00", "low", 120)]))
    assert d1["phases"][0]["label"] == "涨潮"
    d2 = build_phases(make_parsed([("08:00", "low", 100), ("20:00", "high", 350)]))
    assert d2["phases"][0]["label"] == "落潮"


def test_quality_mapping():
    day = build_phases(make_parsed([("12:00", "high", 300), ("23:00", "low", 120)]))
    q = {p["label"]: p["quality"] for p in day["phases"]}
    assert q["平潮"] == 1 and q["转流初期"] == 5 and q["涨潮"] == 4 and q["落潮"] == 2


# ---------- 边界：跨午夜转流窗口 ----------

def test_turn_near_midnight_is_clipped_not_lost():
    """23:50 的高潮：平潮应覆盖到 24:00 截断；其转流窗口溢出到次日——
    已知设计局限：当日表内转流窗口被截断（见测试报告残余风险 R3a）。"""
    day = build_phases(make_parsed([("23:50", "high", 350)]))
    slack = [p for p in day["phases"] if p["label"] == "平潮"]
    assert slack and slack[0]["start_min"] == 23 * 60 + 10 and slack[0]["end_min"] == 1440
    turns = [p for p in day["phases"] if p["label"] == "转流初期"]
    assert turns == []  # 被截断为零长度（记录为已知局限，不是崩溃）


# ---------- summarize_now（含时区正确性探针） ----------

def _days_for_summarize():
    today = build_phases(make_parsed([("03:18", "high", 337), ("10:50", "low", 128), ("15:47", "high", 390)], date="2026-10-02"))
    tomorrow = build_phases(make_parsed([("00:09", "low", 146), ("04:07", "high", 299), ("11:14", "low", 145), ("16:41", "high", 361)], date="2026-10-03"))
    return [today, tomorrow]


def test_summarize_now_returns_current_for_given_time():
    now = datetime_standalone(2026, 10, 2, 5, 0)  # 06:18 前的转流初期
    current, next_turn = summarize_now(_days_for_summarize(), now=now)
    assert current is not None and current["label"] == "转流初期"
    assert current["until"] == "06:18"
    assert next_turn is not None


def test_summarize_now_midnight_boundary_24h():
    # 23:00 → 应落在落潮段且 until 显示 24:00
    now = datetime_standalone(2026, 10, 2, 23, 0)
    current, _ = summarize_now(_days_for_summarize(), now=now)
    assert current["label"] == "落潮"
    assert current["until"] == "24:00"


def test_summarize_now_uses_shanghai_tz_not_local():
    """探针：summarize_now 必须按 Asia/Shanghai 墙上时间判定，
    与运行机器时区无关。传入 UTC 时间 16:10（=上海 10-03 00:10）应命中
    明天 00:39-03:09 的转流初期，而不是拿 16:10 去比上海今天的时间轴。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    now_utc = datetime(2026, 10, 2, 16, 10, tzinfo=ZoneInfo("UTC"))
    current, next_turn = summarize_now(_days_for_summarize(), now=now_utc)
    # 上海 10-03 00:10 → 平潮（00:09 低潮的平潮 23:29(昨)-00:39）
    assert current is not None
    assert current["label"] == "平潮"
    assert current["date"] == "2026-10-03"
    assert next_turn["date"] == "2026-10-03"


def test_default_now_is_shanghai_wall_clock_even_if_machine_abroad(monkeypatch):
    """疫苗（R10，防假疫苗版）：默认 now 必须显式按 Asia/Shanghai 取。

    用假 datetime 模拟"部署机器本地时区=纽约"：修复前 datetime.now()
    （无 tz 参数）会拿到纽约墙上时间，本测试变红；修复后取 TZ_SH，恒绿。
    （单纯把 datetime.now(TZ_SH) 注回成 datetime.now() 在上海时区的
    开发机上测不出差异——注回验证必须模拟异时区机器。）"""
    from datetime import datetime as real_datetime
    from zoneinfo import ZoneInfo

    from server import tide

    class FakeDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                # 模拟机器本地时区 = America/New_York（EST，比上海慢 13 小时）
                return real_datetime(2026, 10, 2, 12, 10)
            return real_datetime(2026, 10, 3, 0, 10, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(tide, "datetime", FakeDatetime)
    current, _ = tide.summarize_now(_days_for_summarize())
    assert current["date"] == "2026-10-03"
    assert current["label"] == "平潮"


def datetime_standalone(*args):
    return __import__("datetime").datetime(*args)
