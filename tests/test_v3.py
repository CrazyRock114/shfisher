"""V3 功能测试：预警解析、水质档案匹配、日志校准分桶。"""

from __future__ import annotations

import pytest

from server.alerts import normalize_flood, parse_warn_js
from server import log_db
from server.data import water_profile_for

WARN_JS_SAMPLE = """
var warns=[]
var historywarns=[{"isActive":false,"yjid":"(2026)3347","htmlword":"【市预警发布中心】防空警报试鸣信息","name":"防空警报试鸣信息","district":"全市","fbsj":"2026-09-19 09:00","jcsj":"2026-09-19 12:00","icon":"fkjb.png"},
{"isActive":true,"yjid":"(2026)3401","htmlword":"【市预警发布中心】上海中心气象台发布暴雨橙色预警信号：预计今天半夜以前中心城区有大暴雨。","yjfbdw":"上海中心气象台","yjfbtype":"发布","name":"暴雨预警","ispj":true,"id":39301,"district":"全市","fbsj":"2026-10-03 06:20","jcsj":null,"setsxtime":"2026-10-03 18:20","lqImage1":null,"icon":"b001.png","deletetag":null,"lockingtag":null,"gtyjstatus":"Actual"}]
"""


# ---------- 预警解析 ----------

def test_parse_warn_js_active_from_history():
    items = parse_warn_js(WARN_JS_SAMPLE)
    # warns 为空，historywarns 里 isActive=true 的那条应被取出生效
    assert len(items) == 1
    a = items[0]
    assert "暴雨橙色预警" in a["title"]
    assert a["level"] == "橙色"
    assert a["fishing_stop"] is True  # 暴雨属于建议停钓关键词
    assert a["unit"] == "上海中心气象台"
    assert a["until"] == "2026-10-03 18:20"


def test_parse_warn_js_no_active():
    assert parse_warn_js("var warns=[]\nvar historywarns=[]") == []


def test_parse_warn_js_malformed_is_loud_not_silent():
    """疫苗（R5）：warns/historywarns 任一段 JSON 坏掉或结构变化必须抛错
    （由 get_alerts 记入 source_errors），不得静默返回空列表把真实预警吞成
    "无生效预警"。特别注意：warns 坏而 historywarns 完好时同样要响。"""
    from server.alerts import parse_warn_js as parse

    with pytest.raises(ValueError, match="JSON 解析失败"):
        parse('var warns=[{"broken": \nvar historywarns=[]')
    with pytest.raises(ValueError, match="不是 JSON 数组"):
        parse('var warns={"a":1}; var historywarns=[]')
    with pytest.raises(ValueError, match="找不到 historywarns"):
        parse('var warns=[]')
    with pytest.raises(ValueError, match="找不到 warns"):
        parse("var something_else=1")


def test_parse_warn_js_realistic_shape_still_ok():
    """疫苗伴生：raw_decode 方式必须仍能读真实结构（warns 在前、historywarns 在后）。"""
    items = parse_warn_js(WARN_JS_SAMPLE)
    assert len(items) == 1 and "暴雨橙色预警" in items[0]["title"]


def test_log_numeric_garbage_coerced_to_none(tmp_path, monkeypatch):
    """疫苗（R4）：rating/score 传非数字字符串必须归一为 None；
    SQLite 亲和性会救 "4"，但救不了 "abc"——不修复则 stats 求和 500。"""
    monkeypatch.setattr(log_db, "DB_PATH", tmp_path / "coerce.db")
    log_db.init_db()
    log_db.add_trip({"date": "2026-10-01", "spot_name": "脏数据", "rating": "abc", "score": "7.5分"})
    stats = log_db.stats()
    assert stats["avg_rating"] is None  # 脏值被剔除而非炸掉
    assert stats["trips"] == 1


def test_normalize_flood_empty_and_active():
    assert normalize_flood([]) is None
    flood = normalize_flood([{"SIGNAL_LEVEL": "Ⅲ", "SIGNAL_STAGE": "启动", "DATETIME": "2026-08-10 06:00"}])
    assert flood["level"] == "Ⅲ"
    assert flood["stage"] if "stage" in flood else True
    assert flood["source"] == "上海市水务局（防汛预警信号）"
    assert flood["fishing_stop"] is False  # Ⅲ 级不触发硬停钓


# ---------- 水质档案 ----------

def test_water_profile_matching():
    tai = water_profile_for("太浦河")
    assert tai["grade"] == "Ⅱ类" and tai["water_body"] == "太浦河"
    dian = water_profile_for("淀山湖")
    assert dian["grade"] == "Ⅲ~Ⅳ类"
    changkou = water_profile_for("黄浦江口/长江口")
    assert changkou["water_body"] == "长江口"
    background = water_profile_for("张家浜")
    assert background["water_body"] == "全市背景" and background["grade"] is None
    assert water_profile_for(None)["water_body"] == "全市背景"


# ---------- 日志校准 ----------

def test_calibration_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr(log_db, "DB_PATH", tmp_path / "cal.db")
    log_db.init_db()
    for score, rating in [(80, 5), (82, 4), (40, 2), (30, 1), (60, 3)]:
        log_db.add_trip(
            {"date": "2026-10-01", "spot_name": "测试", "score": score, "rating": rating}
        )
    cal = log_db.stats()["calibration"]
    assert cal["sample_size"] == 5
    assert not cal["ready"]
    assert cal["buckets"][">=75"]["count"] == 2
    assert cal["buckets"][">=75"]["avg_rating"] == 4.5
    assert cal["buckets"]["<35"]["avg_rating"] == 1


def test_calibration_ignores_missing_score(tmp_path, monkeypatch):
    monkeypatch.setattr(log_db, "DB_PATH", tmp_path / "cal2.db")
    log_db.init_db()
    log_db.add_trip({"date": "2026-10-01", "spot_name": "测试", "rating": 5})
    cal = log_db.stats()["calibration"]
    assert cal["sample_size"] == 0
