"""钓鱼日志存储：SQLite（stdlib sqlite3，无额外依赖）。

每次调用独立开连接（本地文件、低频写入，足够简单可靠）。
渔获明细以自由文本记录（如"鲫鱼x12、鲤鱼1"），统计时尽力解析。

部署形态差异：本地写 data/fisherman.db；Vercel serverless 的应用目录
只读，日志库自动落到 /tmp（实例内存活期内可写，但实例回收即丢失——
serverless 上日志是"会话级"功能，长期数据仍以本地为准）。
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent


def _default_db_path() -> Path:
    if os.environ.get("VERCEL"):  # Vercel：应用目录只读，只有 /tmp 可写
        return Path("/tmp/fisherman.db")
    return ROOT / "data" / "fisherman.db"


DB_PATH = _default_db_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  spot_name TEXT NOT NULL,
  start_time TEXT,
  end_time TEXT,
  score INTEGER,
  weather TEXT,
  fish_text TEXT,
  bait TEXT,
  rating INTEGER,
  notes TEXT,
  created_at TEXT NOT NULL
);
"""

_FIELDS = ["date", "spot_name", "start_time", "end_time", "score", "weather", "fish_text", "bait", "rating", "notes"]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def _to_int_or_none(v: Any) -> Optional[int]:
    """API 层不校验类型，这里兜底：可转 int 则转，否则 None（防止字符串入 stats 求和崩溃）。"""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def add_trip(payload: dict[str, Any]) -> int:
    values = {k: payload.get(k) for k in _FIELDS}
    values["score"] = _to_int_or_none(values.get("score"))
    values["rating"] = _to_int_or_none(values.get("rating"))
    values["created_at"] = datetime.now().isoformat(timespec="seconds")
    if not values["date"] or not values["spot_name"]:
        raise ValueError("date 和 spot_name 为必填")
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO trips ({', '.join(_FIELDS)}, created_at) "
            f"VALUES ({', '.join('?' for _ in _FIELDS)}, ?)",
            [values[k] for k in _FIELDS] + [values["created_at"]],
        )
        return cur.lastrowid


def list_trips(limit: int = 100) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trips ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_trip(trip_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        return cur.rowcount > 0


_FISH_RE = re.compile(r"([\u4e00-\u9fa5]{1,4})\s*[xX×*]\s*(\d{1,3})")


def parse_fish_text(fish_text: Optional[str]) -> dict[str, int]:
    """从"鲫鱼x12、鲤鱼1"这类文本解析各鱼种数量；纯数字按"杂鱼"计。"""
    counts: dict[str, int] = {}
    if not fish_text:
        return counts
    for species, num in _FISH_RE.findall(fish_text):
        counts[species] = counts.get(species, 0) + int(num)
    if not counts and fish_text.strip():
        counts["未注明"] = len(re.findall(r"[、，,;；/]|[\u4e00-\u9fa5]", fish_text)) and fish_text.count("、") + 1
    return counts


def stats() -> dict[str, Any]:
    trips = list_trips(limit=1000)
    total_fish = 0
    species: dict[str, int] = {}
    ratings = [t["rating"] for t in trips if t["rating"]]
    by_spot: dict[str, list[int]] = {}
    for t in trips:
        for sp, n in parse_fish_text(t.get("fish_text")).items():
            species[sp] = species.get(sp, 0) + n
            total_fish += n
        if t["rating"]:
            by_spot.setdefault(t["spot_name"], []).append(t["rating"])

    # 出钓指数校准：按分桶对比"预测评分 vs 实际满意度"
    buckets = {"<35": [], "35-54": [], "55-74": [], ">=75": []}
    for t in trips:
        if t.get("score") and t.get("rating"):
            s = t["score"]
            key = "<35" if s < 35 else "35-54" if s < 55 else "55-74" if s < 75 else ">=75"
            buckets[key].append(t["rating"])
    calibration = {
        "sample_size": sum(len(v) for v in buckets.values()),
        "ready": sum(len(v) for v in buckets.values()) >= 10,
        "buckets": {
            k: {"count": len(v), "avg_rating": round(sum(v) / len(v), 1) if v else None}
            for k, v in buckets.items()
        },
    }

    top_spots = sorted(
        (
            {"spot": s, "trips": len(rs), "avg_rating": round(sum(rs) / len(rs), 1)}
            for s, rs in by_spot.items()
        ),
        key=lambda x: -x["avg_rating"],
    )
    return {
        "trips": len(trips),
        "total_fish": total_fish,
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "species_counts": dict(sorted(species.items(), key=lambda kv: -kv[1])),
        "top_spots": top_spots[:3],
        "calibration": calibration,
    }
