"""潮汐模块测试：用真实页面夹具测解析，用合成潮位表测阶段算法。"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

import pytest

from server.tide import build_phases, parse_tide_html  # noqa
from server import log_db

FIXTURE = Path(__file__).parent / "fixtures" / "msa_tide_wusong.html"


@pytest.fixture(scope="module")
def parsed():
    return parse_tide_html(FIXTURE.read_text(encoding="utf-8"))


# ---------- 解析 ----------

def test_parse_title(parsed):
    assert parsed["station"] == "吴淞"
    assert parsed["date"] == "2026-10-02"


def test_parse_stands(parsed):
    assert parsed["stands"] == [
        {"time": "03:18", "height": 337, "type": "high"},
        {"time": "10:50", "height": 128, "type": "low"},
        {"time": "15:47", "height": 390, "type": "high"},
    ]


def test_parse_hourly(parsed):
    assert len(parsed["hourly"]) == 24
    assert parsed["hourly"][0] == 144
    assert parsed["hourly"][15] == 382
    assert parsed["hourly"][16] == 389


# ---------- 阶段算法 ----------

def test_phases_structure_and_no_overlap(parsed):
    day = build_phases(parsed)
    assert len(day["phases"]) >= 4
    prev_end = 0
    for ph in day["phases"]:
        assert ph["start_min"] == prev_end  # 无缝时间轴
        assert ph["end_min"] > ph["start_min"]
        assert ph["label"] in ("涨潮", "落潮", "平潮", "转流初期")
        assert 1 <= ph["quality"] <= 5
        prev_end = ph["end_min"]
    assert prev_end == 1440


def test_slack_around_stand(parsed):
    # 高潮 03:18 → 02:38-03:48 应为平潮；03:48 起转流初期
    labels = {(ph["label"], ph["start_min"], ph["end_min"]) for ph in build_phases(parsed)["phases"]}
    assert any(l == "平潮" and s <= 158 <= e for l, s, e in labels)
    assert any(l == "转流初期" and s <= 228 + 30 <= e for l, s, e in labels)


def test_flood_before_first_high(parsed):
    # 首个 stand 是高潮 03:18 → 00:00 起应为涨潮
    first = build_phases(parsed)["phases"][0]
    assert first["label"] == "涨潮"
    assert first["start_min"] == 0


def test_turn_after_low_is_longest_quality_5(parsed):
    day = build_phases(parsed)
    turns = [ph for ph in day["phases"] if ph["label"] == "转流初期"]
    assert len(turns) == 3
    assert all(ph["quality"] == 5 for ph in turns)
    assert all(ph["end_min"] - ph["start_min"] == 150 for ph in turns)


# ---------- 日志 ----------

def test_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(log_db, "DB_PATH", tmp_path / "test.db")
    log_db.init_db()
    tid = log_db.add_trip(
        {
            "date": "2026-10-02",
            "spot_name": "张家浜·云山路口",
            "start_time": "06:00",
            "end_time": "10:00",
            "score": 75,
            "weather": '{"temp":21,"wind":"东北风2级"}',
            "fish_text": "鲫鱼x12、鲤鱼x1、白条若干",
            "bait": "老三样",
            "rating": 4,
            "notes": "涨潮窗口连竿",
        }
    )
    trips = log_db.list_trips()
    assert len(trips) == 1 and trips[0]["id"] == tid
    stats = log_db.stats()
    assert stats["trips"] == 1
    assert stats["species_counts"]["鲫鱼"] == 12
    assert stats["total_fish"] == 13
    assert stats["avg_rating"] == 4
    assert stats["top_spots"][0]["spot"] == "张家浜·云山路口"
    assert log_db.delete_trip(tid) is True
    assert log_db.list_trips() == []
