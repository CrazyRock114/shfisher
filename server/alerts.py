"""预警聚合：气象/人文预警（上海市预警发布中心）+ 防汛预警（市水务局）。

数据源（2026-10 探测核实，均为官方免费公开）：
1. 上海市预警发布中心 warn.js —— wx.soweather.com/wxapp/jsondata/warn.js
   JS 变量文件：var warns=[...生效中...]; var historywarns=[...含 isActive...];
   字段：htmlword(全文)、yjfbdw(发布单位)、name、district、fbsj(发布)、jcsj(解除)、isActive。
2. 市水务局 getCurrentXy —— 防汛预警信号（无预警时 data 为空数组）。

输出统一为 {active:[...], flood:{...}|null}，前端渲染全局横幅。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

SOWEATHER_WARN_URL = "http://wx.soweather.com/wxapp/jsondata/warn.js"
SWJ_XY_URL = "https://swgxh.swj.sh.gov.cn/swj/swj/businessConvenientService/getCurrentXy"

ALERTS_TTL_SECONDS = 300  # 5 分钟缓存

_cache: dict[str, tuple[float, Any]] = {}
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fisherman-app/0.2"}

# 出钓相关的高危预警关键词 → 横幅建议
FISHING_STOP_KEYWORDS = ("暴雨", "台风", "雷电", "大风", "寒潮")


def parse_warn_js(text: str) -> list[dict[str, Any]]:
    """解析上海预警发布中心 warn.js：取生效中 + 历史里仍标记 isActive 的条目。

    warns 与 historywarns 两个变量缺一不可，任何一段缺失或 JSON 非法都抛
    ValueError（由 get_alerts 记入 source_errors）——静默返回空列表会让真实
    预警被吞成"无生效预警"。用 raw_decode 逐变量取值，避免正则跨段误配。
    """
    warns = _extract_var_array(text, "warns")
    history = _extract_var_array(text, "historywarns")
    return normalize_alerts(warns + [x for x in history if x.get("isActive")])


def _extract_var_array(text: str, name: str) -> list:
    m = re.search(rf"var\s+{name}\s*=", text)
    if not m:
        raise ValueError(f"warn.js 结构不符预期：找不到 {name} 变量（页面可能已改版）")
    if not re.match(r"\s*\[", text[m.end():]):
        raise ValueError(f"warn.js 的 {name} 段不是 JSON 数组")
    idx = m.end() + re.match(r"\s*", text[m.end():]).end()
    try:
        data, _ = json.JSONDecoder().raw_decode(text[idx:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"warn.js 的 {name} 段 JSON 解析失败：{exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"warn.js 的 {name} 段不是 JSON 数组")
    return data


def normalize_alerts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        title = (it.get("htmlword") or it.get("name") or "").strip()
        if not title:
            continue
        level = next((c for c in ("红色", "橙色", "黄色", "蓝色") if c in title), None)
        out.append(
            {
                "title": title,
                "name": it.get("name"),
                "unit": it.get("yjfbdw"),
                "district": it.get("district"),
                "published": it.get("fbsj"),
                "until": it.get("jcsj") or it.get("setsxtime"),
                "level": level,
                "fishing_stop": any(k in title for k in FISHING_STOP_KEYWORDS),
                "source": "上海市预警发布中心",
            }
        )
    return out


def normalize_flood(data: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not data:
        return None
    x = data[0]
    level = str(x.get("SIGNAL_LEVEL") or "").strip() or None
    stage = str(x.get("SIGNAL_STAGE") or "").strip() or None
    title = (x.get("htmlword") or x.get("CONTENT") or "").strip() or f"防汛预警响应（{level or '级别未知'}·{stage or '状态未知'}）"
    return {
        "title": title,
        "name": "防汛预警响应",
        "unit": "上海市水务局",
        "district": "全市",
        "published": x.get("DATETIME") or x.get("fbsj"),
        "until": None,
        "level": level,
        "fishing_stop": stage == "启动" and level in ("Ⅰ", "Ⅱ", "一", "二", "1", "2"),
        "source": "上海市水务局（防汛预警信号）",
    }


async def get_alerts() -> dict[str, Any]:
    from datetime import datetime

    hit = _cache.get("alerts")
    if hit and time.monotonic() - hit[0] < ALERTS_TTL_SECONDS:
        return hit[1]

    active: list[dict[str, Any]] = []
    flood = None
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=15, headers=_UA) as client:
        try:
            resp = await client.get(SOWEATHER_WARN_URL)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            active = parse_warn_js(resp.text)
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"市预警发布中心：{exc}")
        try:
            resp = await client.get(SWJ_XY_URL)
            resp.raise_for_status()
            flood = normalize_flood(resp.json().get("data", []))
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"市水务局：{exc}")

    if flood:
        active = [flood, *active]

    result = {
        "active": active,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_errors": errors,
        "summary": "；".join(a["title"][:60] for a in active[:2]) if active else "当前无生效预警",
    }
    _cache["alerts"] = (time.monotonic(), result)
    return result
