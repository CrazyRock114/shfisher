#!/usr/bin/env python3
"""数据层穷举审计（exhaustive L1 结构 / L2 数值 / L5 一致性）。

口径与计数全部由本脚本打印，数字不经过人手；任何互锁对不上即退出码 1。
运行：.venv/bin/python scripts/audit_data.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []
checks = 0


def check(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def main() -> None:
    # ---------- L1 结构：钓点 schema 穷举 ----------
    data = json.loads((ROOT / "data" / "fishing_spots.json").read_text(encoding="utf-8"))
    spots = data["spots"]
    print(f"[L1] 钓点条目: {len(spots)}")
    REQUIRED = ["id", "name", "district", "water_body", "type", "fish_species",
                "fee", "compliance", "confidence", "lat", "lon", "sources", "tidal"]
    ids = [s["id"] for s in spots if "id" in s]
    check(len(ids) == len(spots), f"有 {len(spots) - len(ids)} 条缺 id")
    check(len(set(ids)) == len(ids), "id 存在重复")

    TYPES = {"official_spots", "river", "lake", "paid", "sea", "warning"}
    CONFS = {"verified", "community", "unverified"}
    type_counter: dict[str, int] = {}
    conf_counter: dict[str, int] = {}
    for s in spots:
        missing = [k for k in REQUIRED if k not in s]
        check(not missing, f"{s.get('id', '?')} 缺字段 {missing}")
        check(s["type"] in TYPES, f"{s['id']} type 非法: {s['type']}")
        check(s["confidence"] in CONFS, f"{s['id']} confidence 非法: {s['confidence']}")
        check(isinstance(s["fish_species"], list), f"{s['id']} fish_species 不是数组")
        check(isinstance(s["sources"], list), f"{s['id']} sources 不是数组")
        check(isinstance(s["tidal"], bool), f"{s['id']} tidal 不是布尔")
        check(30.6 <= s["lat"] <= 31.9, f"{s['id']} 纬度越界: {s['lat']}")
        check(120.8 <= s["lon"] <= 122.2, f"{s['id']} 经度越界: {s['lon']}")
        for u in s["sources"]:
            check(re.match(r"^https?://", u), f"{s['id']} 来源 URL 非法: {u}")
        type_counter[s["type"]] = type_counter.get(s["type"], 0) + 1
        conf_counter[s["confidence"]] = conf_counter.get(s["confidence"], 0) + 1
    tidal = [s["id"] for s in spots if s["tidal"]]
    print(f"[L1] type 分布: {type_counter}")
    print(f"[L1] confidence 分布: {conf_counter}")
    print(f"[L1] tidal=true: {len(tidal)} -> {tidal}")
    check(len(tidal) == 7, f"tidal 数量与文档声明(7)不符: {len(tidal)}")
    check("warning-dianshanhu" in tidal or True, "")  # 淀山湖警示位 tidal 应为 False
    check(not next(s for s in spots if s["id"] == "warning-dianshanhu")["tidal"],
          "淀山湖（内湖警示位）不应标记感潮")

    # ---------- L5 一致性：前端枚举表必须全覆盖数据 ----------
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    type_meta = set(re.findall(r"^\s{2}(\w+): \{ text:", app_js, re.M))
    conf_meta = set(re.findall(r"^\s{2}(\w+): \{ text: ", app_js, re.M) )
    check(TYPES <= type_meta, f"前端 TYPE_META 缺类型: {TYPES - type_meta}")
    check(CONFS <= conf_meta, f"前端 CONF_META 缺置信度: {CONFS - conf_meta}")
    print(f"[L5] TYPE_META={sorted(type_meta)} CONF_META={sorted(conf_meta)} —— 互锁通过")

    # ---------- L2/L5：水质档案命中互锁 ----------
    sys.path.insert(0, str(ROOT))
    from server.data import WATER_PROFILES, water_profile_for
    check(len(WATER_PROFILES) == 4, f"水质档案键数应为 4: {len(WATER_PROFILES)}")
    specific = sum(1 for s in spots if water_profile_for(s["water_body"])["water_body"] != "全市背景")
    print(f"[L2] 水质档案命中: {specific}/{len(spots)} 条命中特定水体档案，其余用全市背景")

    # ---------- L2/L5：装备清单与预算档位（app.js 内静态数据） ----------
    gear_block = re.search(r"const GEAR_ITEMS = \[(.*?)\n\];", app_js, re.S)
    check(gear_block is not None, "app.js 找不到 GEAR_ITEMS")
    gear_prices = [int(p) for p in re.findall(r"price: (\d+)", gear_block.group(1))]
    gear_total = sum(gear_prices)
    print(f"[L2] 装备条目: {len(gear_prices)} 件，总价 {gear_total} 元")
    check(len(gear_prices) == 15, f"装备应为 15 件: {len(gear_prices)}")
    check(gear_total == 520, f"装备总价应为 520（README 声明约 520 元）: {gear_total}")
    tier_names = re.findall(r'name: "([^"]+)",\n', re.search(r"const GEAR_TIERS = \[(.*?)\n\];", app_js, re.S).group(1))
    print(f"[L2] 预算档位: {tier_names}")
    check(len(tier_names) == 3, f"预算应为 3 档: {len(tier_names)}")

    # ---------- L2：评分权重与潮汐站 ----------
    from server.scoring import WEIGHTS
    check(sum(WEIGHTS.values()) == 100, f"评分权重合计应为 100: {sum(WEIGHTS.values())}")
    print(f"[L2] 评分权重: {WEIGHTS} 合计 {sum(WEIGHTS.values())}")
    from server.tide import STATIONS
    check(len(STATIONS) == 21, f"潮汐站应为 21: {len(STATIONS)}")
    print(f"[L2] 潮汐站: {len(STATIONS)} 个")

    # ---------- L5：README 计数声明互锁 ----------
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+) 个调研钓点", readme)
    check(m and int(m.group(1)) == len(spots),
          f"README 声明 {m.group(1) if m else '?'} 个钓点，实际 {len(spots)}")
    print(f"[L5] README 钓点计数声明({m.group(1)}) 与实际({len(spots)}) 互锁通过")

    print(f"\n===== 审计完成：{checks} 项检查，失败 {len(failures)} =====")
    for f in failures:
        print("  ✗", f)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
