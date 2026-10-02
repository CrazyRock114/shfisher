"""fisherman MVP 后端：FastAPI 服务。

- GET /api/score      出钓指数（Open-Meteo 逐小时 + scoring.py 透明评分，含 10 分钟缓存）
- GET /api/spots      钓点库
- GET /api/compliance 合规速查卡
- 静态页面挂载在 /（web/ 目录，零构建）

启动：uvicorn server.main:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import log_db, tide
from .alerts import get_alerts
from .data import COMPLIANCE, DEFAULT_LOCATION, SPOTS, WATER_PROFILES
from .scoring import compute_scores

TZ = ZoneInfo("Asia/Shanghai")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL_SECONDS = 600  # 天气 10 分钟缓存，单城市个人用量下远低于免费额度

# 静态资源用绝对路径定位：本地与 Vercel serverless（/var/task）均成立
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="fisherman API", version="0.3.0")
_cache: dict[tuple, tuple[float, dict]] = {}
log_db.init_db()


async def _fetch_forecast(lat: float, lon: float) -> dict:
    key = ("openmeteo", round(lat, 3), round(lon, 3))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "wind_speed_10m",
                "wind_gusts_10m",
                "wind_direction_10m",
                "pressure_msl",
            ]
        ),
        "past_days": 2,
        "forecast_days": 3,
        "timezone": "Asia/Shanghai",
        "wind_speed_unit": "ms",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"天气数据源请求失败：{exc}") from exc
    _cache[key] = (time.monotonic(), payload)
    return payload


@app.get("/api/spots")
async def api_spots() -> dict:
    return {"spots": SPOTS, "default_location": DEFAULT_LOCATION}


@app.get("/api/compliance")
async def api_compliance() -> dict:
    return {"items": COMPLIANCE}


@app.get("/api/tide")
async def api_tide(
    station: str = Query(default=tide.DEFAULT_STATION, max_length=20),
) -> dict:
    try:
        return await tide.get_tide(station)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"潮汐数据源请求失败：{exc}") from exc


@app.get("/api/waterlevel")
async def api_waterlevel() -> dict:
    try:
        return await tide.get_water_level()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"水位数据源请求失败：{exc}") from exc


@app.get("/api/waterquality")
async def api_waterquality() -> dict:
    return {"profiles": WATER_PROFILES}


@app.get("/api/alerts")
async def api_alerts() -> dict:
    return await get_alerts()


@app.get("/api/log")
async def api_log_list() -> dict:
    return {"trips": log_db.list_trips(), "stats": log_db.stats()}


@app.post("/api/log")
async def api_log_add(payload: dict = Body(...)) -> dict:
    try:
        trip_id = log_db.add_trip(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": trip_id}


@app.delete("/api/log/{trip_id}")
async def api_log_delete(trip_id: int) -> dict:
    if not log_db.delete_trip(trip_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True}


@app.get("/api/score")
async def api_score(
    lat: float = Query(default=None, ge=-90, le=90),
    lon: float = Query(default=None, ge=-180, le=180),
    label: str = Query(default=None, max_length=80),
) -> dict:
    loc = DEFAULT_LOCATION if lat is None or lon is None else {"lat": lat, "lon": lon}
    label = label or (DEFAULT_LOCATION["label"] if lat is None else f"{lat:.3f}, {lon:.3f}")
    forecast = await _fetch_forecast(loc["lat"], loc["lon"])
    now = datetime.now(TZ)
    try:
        result = compute_scores(forecast, now)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result["location"] = {"lat": loc["lat"], "lon": loc["lon"], "label": label}
    result["generated_at"] = now.isoformat(timespec="seconds")
    return result


# 静态前端（web/）挂在根路径
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
