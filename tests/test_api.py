"""API 集成测试（FastAPI TestClient）。

只覆盖不依赖外部网络的端点与错误路径；依赖官方数据源的端点
（score/tide 默认/waterlevel/alerts）由 scripts/live_smoke.sh 做联调冒烟。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import log_db
from server.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(log_db, "DB_PATH", tmp_path / "api-test.db")
    log_db.init_db()
    with TestClient(app) as c:
        yield c


# ---------- 静态数据端点 ----------

def test_get_spots_shape(client):
    r = client.get("/api/spots")
    assert r.status_code == 200
    data = r.json()
    assert len(data["spots"]) == 47
    assert data["default_location"]["label"] == "上海市中心"


def test_get_compliance(client):
    r = client.get("/api/compliance")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 12


def test_get_waterquality(client):
    r = client.get("/api/waterquality")
    assert r.status_code == 200
    assert set(r.json()["profiles"].keys()) == {"太浦河", "淀山湖", "长江口", "背景"}


# ---------- 参数与错误路径 ----------

def test_tide_unknown_station_is_400_not_5xx(client):
    r = client.get("/api/tide", params={"station": "不存在的站"})
    assert r.status_code == 400
    assert "可选" in r.json()["detail"]


def test_score_rejects_out_of_range_coords(client):
    r = client.get("/api/score", params={"lat": 999, "lon": 0})
    assert r.status_code == 422  # Query 约束生效


# ---------- 日志 CRUD 全周期 ----------

def test_log_crud_cycle(client):
    payload = {
        "date": "2026-10-02",
        "spot_name": "测试钓点",
        "start_time": "06:00",
        "end_time": "10:00",
        "score": 75,
        "weather": '{"temp":21}',
        "fish_text": "鲫鱼x3",
        "bait": "老三样",
        "rating": 4,
        "notes": "ok",
    }
    r = client.post("/api/log", json=payload)
    assert r.status_code == 200
    tid = r.json()["id"]

    lst = client.get("/api/log").json()
    assert any(t["id"] == tid for t in lst["trips"])
    assert lst["stats"]["trips"] == 1

    assert client.delete(f"/api/log/{tid}").status_code == 200
    assert client.get("/api/log").json()["stats"]["trips"] == 0


def test_log_missing_required_field_is_400(client):
    r = client.post("/api/log", json={"date": "2026-10-02"})  # 缺 spot_name
    assert r.status_code == 400


def test_log_delete_nonexistent_is_404(client):
    assert client.delete("/api/log/99999").status_code == 404


def test_log_numeric_fields_are_coerced(client):
    """探针（R4）：rating/score 传字符串应被规范化，否则 stats 求和会 500。"""
    r = client.post(
        "/api/log",
        json={"date": "2026-10-02", "spot_name": "类型测试", "rating": "4", "score": "75"},
    )
    assert r.status_code == 200
    stats = client.get("/api/log").json()["stats"]
    # 修复前：rating 存成字符串，avg_rating 抛 TypeError → 接口 500
    assert stats["avg_rating"] == 4
    assert stats["trips"] == 1
