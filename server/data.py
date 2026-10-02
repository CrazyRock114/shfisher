"""静态数据层：钓点库与合规速查卡。

钓点来自 data/fishing_spots.json（2026-10 调研，含来源与置信度）；
合规条目整理自 docs/00 与 docs/01 的官方来源，均标注原文链接。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with open(ROOT / "data" / "fishing_spots.json", encoding="utf-8") as f:
    _SPOTS_DATA = json.load(f)

SPOTS: list[dict] = _SPOTS_DATA["spots"]

# 默认查询点：上海市中心（人民广场附近）
DEFAULT_LOCATION = {"lat": 31.2304, "lon": 121.4737, "label": "上海市中心"}

# 水质钓点档案：只收录有官方来源的水体等级，缺失的不硬编。
# 水质是月级长期标签（对单次出钓参考有限），月报更新后请人工刷新 as_of。
WATER_PROFILES: dict[str, dict] = {
    "太浦河": {
        "grade": "Ⅱ类",
        "note": "长三角一体化示范区水源通道，近年稳定Ⅱ类，鱼活性与食用安全性好",
        "source": "https://view.inews.qq.com/a/20260606A03SUP00",
        "as_of": "2026-06",
    },
    "淀山湖": {
        "grade": "Ⅲ~Ⅳ类",
        "note": "区级监测：急水港桥断面Ⅳ类、四号航标Ⅲ类；夏季有蓝藻风险，钓点宜选湖区东南侧进水口",
        "source": "https://www.shqp.gov.cn/env/shjzk/20240919/1210876.html",
        "as_of": "2024-09",
    },
    "长江口": {
        "grade": "Ⅱ类",
        "note": "长江浏河、陈行水库等断面为Ⅱ类；注意禁捕管理区边界",
        "source": "https://sthj.sh.gov.cn/hbzhywpt1143/hbzhywpt1149/20260727/f564f94a9a0048b8a7aaefc2191d8da4.html",
        "as_of": "2026-06",
    },
    "背景": {
        "grade": None,
        "note": "全市地表水优Ⅲ类断面占 98.9%（2026 年 1-6 月市生态环境局月报）；Ⅱ-Ⅲ类水鱼活性与食用安全性较好，Ⅳ类及以下溶氧波动大",
        "source": "https://sthj.sh.gov.cn/hbzhywpt1143/hbzhywpt1149/20260727/f564f94a9a0048b8a7aaefc2191d8da4.html",
        "as_of": "2026-06",
    },
}


def water_profile_for(water_body: str) -> dict | None:
    """按水体名匹配水质档案（键为水体名子串匹配）；匹配不到返回背景档案。"""
    if water_body:
        for key, profile in WATER_PROFILES.items():
            if key != "背景" and (key in water_body or water_body in key):
                return {**profile, "water_body": key}
    return {**WATER_PROFILES["背景"], "water_body": "全市背景"}


COMPLIANCE: list[dict] = [
    {
        "category": "竿钩方式",
        "item": "一人一杆、一线、一钩",
        "rule": "无倒刺钩更优；禁止串钩、爆炸钩、一人多杆、多线多钩、长线多钩——上述行为会被认定为生产性捕捞",
        "source": "https://www.shanghai.gov.cn/gwk/search/content/60557f0011a24d8d9755fe252bca15c0",
    },
    {
        "category": "禁渔期",
        "item": "每年 2 月 16 日 12 时 – 5 月 16 日 12 时",
        "rule": "上海内陆水域禁渔期；期间禁止生产性捕捞，个人休闲垂钓（一杆一钩）仍可进行",
        "source": "https://nyncw.sh.gov.cn/shsnwgfxwj/20260225/d4f65f51ede44ff8b4019397df10d4ad.html",
    },
    {
        "category": "禁钓区",
        "item": "长江口中华鲟自然保护区、长江刀鲚种质资源保护区核心区",
        "rule": "常年禁钓",
        "source": "https://www.shanghai.gov.cn/gwk/search/content/60557f0011a24d8d9755fe252bca15c0",
    },
    {
        "category": "禁钓区",
        "item": "饮用水源一级保护区",
        "rule": "金泽水库周边、淀山湖部分岸线（西岸 500 米、急水港北岸等）、青草沙等，依《水污染防治法》禁钓；违者罚 200–500 元",
        "source": "https://www.ksrmtzx.com/news/detail/74887",
    },
    {
        "category": "禁钓区",
        "item": "崇明 4 处禁钓区",
        "rule": "张网港、北堡港以西（刀鲚保护区核心区）、上海长江大桥以东水域、北八滧港以东（中华鲟保护区）",
        "source": "https://sghexport.shobserver.com/html/toutiao/2023/02/28/971403.html",
    },
    {
        "category": "禁钓区",
        "item": "郊野公园生态保育区",
        "rule": "如青西郊野公园生态保育区禁钓，园内其他区域以公园公告为准",
        "source": "https://www.shqp.gov.cn/shqp/sjtjzt/sjyw/20220803/956203.html",
    },
    {
        "category": "水域管理",
        "item": "黄浦江 / 苏州河",
        "rule": "仅在官方垂钓点（白莲泾公园 2 处、苏州河沿岸 7 处）可安心下竿；未设点岸段可能被城管劝导",
        "source": "https://news.xinmin.cn/2023/03/29/32348713.html",
    },
    {
        "category": "饵料",
        "item": "禁有毒有害饵料与部分活饵",
        "rule": "泥鳅、虾类等活体水生生物做饵被新《渔业法》禁止；蚯蚓属陆生无碍；红虫属灰色地带，黑坑多禁用",
        "source": "https://news.qq.com/rain/a/20230924A01RI300",
    },
    {
        "category": "设备",
        "item": "可视探鱼设备与船艇",
        "rule": "长江口禁捕管理区明文禁止任何形式的可视性探鱼辅助渔具、锚鱼、船艇排筏垂钓；声呐探鱼器在非禁渔区属灰色地带，以属地细则为准（建议拨 12316 核实）",
        "source": "https://www.shanghai.gov.cn/gwk/search/content/60557f0011a24d8d9755fe252bca15c0",
    },
    {
        "category": "渔获",
        "item": "禁买卖渔获物",
        "rule": "误钓国家保护水生动物须及时报告并救护放生",
        "source": "https://www.shanghai.gov.cn/gwk/search/content/60557f0011a24d8d9755fe252bca15c0",
    },
    {
        "category": "处罚量级",
        "item": "参考区间",
        "rule": "水源一级保护区垂钓罚 200–500 元；江苏违规垂钓罚 200–2000 元；生产性捕捞情节严重可触《刑法》第 340 条（非法捕捞水产品罪）",
        "source": "https://jsnews.jschina.com.cn/yz/a/202009/t20200910_2626382.shtml",
    },
    {
        "category": "咨询核实",
        "item": "上海三农热线 021-12316",
        "rule": "出发前对具体水域拨打核实属地最新通告；全市统一的'野钓河段开放清单'尚未集中公布",
        "source": "",
    },
]
