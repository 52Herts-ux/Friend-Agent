"""高德地图工具：路径规划、地址转坐标、POI 搜索（需 AMAP_API_KEY）。

高德 Web 服务 API（https://restapi.amap.com）：
- route   路径规划（driving 驾车 / walking 步行 / transit 公交 / riding 骑行）
- geocode 地址 → 经纬度
- search  POI 关键词搜索（找地点/周边）

起点/终点支持地址名或「lng,lat」坐标，地址名会自动先做地理编码转坐标。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from ..base import Tool

AMAP_KEY = os.environ.get("AMAP_API_KEY", "").strip()
# 每月高德 API 额度上限（可在 config/.env 用 AMAP_MONTHLY_LIMIT 覆盖）
AMAP_MONTHLY_LIMIT = int(os.environ.get("AMAP_MONTHLY_LIMIT", "30000"))
_USAGE_PATH = Path("data/amap_usage.json")
BASE = "https://restapi.amap.com"

# 每个操作对应的真实 API 调用权重（route 内部会 geocode 起点+终点 + direction）
_OP_WEIGHT = {"route": 3, "geocode": 1, "search": 1}


def _load_usage() -> dict:
    if _USAGE_PATH.exists():
        try:
            return json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_usage(usage: dict) -> None:
    try:
        _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USAGE_PATH.write_text(json.dumps(usage, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass


def _check_quota(operation: str) -> str:
    """检查本月额度并计数，超限返回错误提示，否则返回空串。"""
    month = time.strftime("%Y-%m")
    usage = _load_usage()
    m = usage.get(month, {"count": 0, "ops": {}})
    weight = _OP_WEIGHT.get(operation, 1)
    if m["count"] + weight > AMAP_MONTHLY_LIMIT:
        return (f"⚠️ 本月高德 API 额度已用完（{m['count']}/{AMAP_MONTHLY_LIMIT} 次），"
                "本次调用被拦截，下月自动恢复")
    m["count"] += weight
    ops = m.setdefault("ops", {})
    ops[operation] = ops.get(operation, 0) + 1
    usage[month] = m
    _save_usage(usage)
    return ""

# 出行方式 → API 端点
_DIRECTIONS = {
    "driving": "/v3/direction/driving",
    "walking": "/v3/direction/walking",
    "transit": "/v3/direction/transit/integrated",
    "riding": "/v4/direction/bicycling",
}
_MODE_LABEL = {"driving": "驾车", "walking": "步行", "transit": "公交", "riding": "骑行"}


def _geocode(address: str, city: str = "") -> str:
    """地址 → 经纬度 'lng,lat'（找不到返回空串）。

    city 可限定城市；多个候选时优先取：① city 匹配的，② 高等级（公交地铁站点/道路），
    避免把「黄龙山地铁站」这种模糊地名匹配到外省的村庄。
    """
    params = {"address": address, "key": AMAP_KEY}
    if city:
        params["city"] = city
    resp = requests.get(f"{BASE}/v3/geocode/geo", params=params, timeout=10)
    data = resp.json()
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return ""
    # ① 优先取 city 匹配的候选
    if city:
        for g in geocodes:
            if city in (g.get("formatted_address") or ""):
                return g.get("location", "")
    # ② 优先取高等级候选（避免村庄/山区）
    for level in ("公交地铁站点", "道路", "乡镇", "区县", "城市"):
        for g in geocodes:
            if g.get("level") == level:
                return g.get("location", "")
    return geocodes[0].get("location", "")


def _to_coord(s: str, city: str = "") -> str:
    """把地址名或坐标统一转成 'lng,lat' 坐标。"""
    s = (s or "").strip()
    if not s:
        return ""
    # 已是坐标格式（如 114.3,30.5）
    parts = s.split(",")
    if len(parts) == 2 and all(p.replace(".", "").replace("-", "").isdigit()
                               for p in parts):
        return s
    return _geocode(s, city)


def _fmt_km(meters) -> str:
    m = int(meters or 0)
    return f"{m/1000:.1f} 公里" if m >= 1000 else f"{m} 米"


class AmapTool(Tool):
    name = "amap"
    description = (
        "调用高德地图：规划路线（驾车/步行/公交/骑行）、地址转坐标、搜索地点。"
        "当用户需要查路线、导航、找地方、问两地怎么走、查地址坐标时使用。"
        "注意：查路线或地址时，如果地点是常见地名（如「光谷广场」），务必同时传 city 城市参数"
        "（如「武汉」），否则同名地点可能匹配到其他城市导致结果错误。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["route", "geocode", "search"],
                "description": "操作类型：route=路线规划，geocode=地址转坐标，search=搜索地点",
            },
            "origin": {"type": "string", "description": "起点（地址名或 lng,lat 坐标），route 用"},
            "destination": {"type": "string", "description": "终点（地址名或 lng,lat 坐标），route 用"},
            "mode": {
                "type": "string",
                "enum": ["driving", "walking", "transit", "riding"],
                "description": "出行方式，route 用，默认 driving",
            },
            "address": {"type": "string", "description": "要转坐标的地址，geocode 用"},
            "keywords": {"type": "string", "description": "搜索关键词，search 用"},
            "city": {"type": "string", "description": "限定城市（如「武汉」），route/geocode/search 均可用，避免同名地点匹配错"},
        },
        "required": ["operation"],
    }

    async def run(self, operation: str = "", **kwargs) -> str:
        if not AMAP_KEY:
            return "高德地图 key 未配置，请在 config/.env 里设置 AMAP_API_KEY"
        op = (operation or "").strip().lower()
        # 额度检查：超限直接拦截，不消耗 API
        if op in _OP_WEIGHT:
            quota_msg = _check_quota(op)
            if quota_msg:
                return quota_msg
        try:
            if op == "route":
                return await self._route(kwargs.get("origin", ""),
                                         kwargs.get("destination", ""),
                                         kwargs.get("mode", "driving"),
                                         kwargs.get("city", ""))
            if op == "geocode":
                return self._do_geocode(kwargs.get("address", ""),
                                        kwargs.get("city", ""))
            if op == "search":
                return self._do_search(kwargs.get("keywords", ""),
                                       kwargs.get("city", ""))
            return f"未知操作 `{op}`（可用：route / geocode / search）"
        except Exception as exc:
            return f"高德地图调用失败: {exc}"

    # ---- 路径规划 ----
    async def _route(self, origin: str, destination: str, mode: str,
                     city: str = "") -> str:
        if not origin or not destination:
            return "需要起点和终点（如「武汉大学」到「光谷广场」）"
        mode = (mode or "driving").lower()
        if mode not in _DIRECTIONS:
            return f"不支持的出行方式 `{mode}`（可用：driving/walking/transit/riding）"
        o = _to_coord(origin, city)
        d = _to_coord(destination, city)
        if not o or not d:
            return "无法解析起点/终点坐标，请用更具体的地名或加上 city 城市"
        resp = requests.get(f"{BASE}{_DIRECTIONS[mode]}",
                            params={"origin": o, "destination": d,
                                    "key": AMAP_KEY, "extensions": "base"},
                            timeout=15)
        data = resp.json()
        if mode == "riding":
            # v4 骑行 API 无 status 字段，直接看 data.paths
            if not (data.get("data", {}).get("paths")):
                return f"路线规划失败: {data.get('info', '未知错误')}"
        elif data.get("status") != "1":
            return f"路线规划失败: {data.get('info', '未知错误')}"
        return self._format_route(data, mode, origin, destination)

    def _format_route(self, data: dict, mode: str, origin: str, destination: str) -> str:
        label = _MODE_LABEL.get(mode, mode)
        head = f"{origin} → {destination} 的{label}路线："
        if mode == "transit":
            transits = data.get("route", {}).get("transits") or []
            if not transits:
                return head + "\n没有找到公交方案"
            # 按耗时排序，最快的方案排前面（高德默认把慢方案排前面）
            transits = sorted(transits, key=lambda t: int(t.get("duration", 0) or 0))
            lines = [head + f"（{len(transits)} 个方案，按时间排序）"]
            for i, t in enumerate(transits[:3], 1):
                dur = int(t.get("duration", 0)) // 60
                dist = _fmt_km(t.get("distance", 0))
                cost = t.get("cost", "未知")
                lines.append(f"  方案{i}：约 {dur} 分钟 · {dist} · 票价 {cost} 元")
            return "\n".join(lines)
        # driving/walking/riding：paths 结构（riding 用 data.paths，其余 route.paths）
        paths = (data.get("route", {}).get("paths")
                 or data.get("data", {}).get("paths") or [])
        if not paths:
            return head + "\n没有找到路线"
        p = paths[0]
        dur = int(p.get("duration", 0)) // 60
        dist = _fmt_km(p.get("distance", 0))
        steps = p.get("steps") or []
        result = head + f"\n全程约 {dur} 分钟 · {dist}"
        if mode == "driving":
            tolls = p.get("tolls")
            if tolls:
                result += f" · 过路费约 {tolls} 元"
        if steps:
            result += f"\n途经 {len(steps)} 段，主要路段："
            for s in steps[:5]:
                road = s.get("roads") or s.get("road") or ""
                instruction = s.get("instruction") or ""
                if road:
                    result += f"\n  · {road}"
                elif instruction:
                    result += f"\n  · {instruction}"
        return result

    # ---- 地理编码 ----
    def _do_geocode(self, address: str, city: str = "") -> str:
        if not address:
            return "需要地址（如「武汉大学」）"
        loc = _geocode(address, city)
        if not loc:
            return f"找不到「{address}」的坐标"
        lng, lat = loc.split(",")
        return f"「{address}」的坐标：经度 {lng}，纬度 {lat}（{loc}）"

    # ---- POI 搜索 ----
    def _do_search(self, keywords: str, city: str = "") -> str:
        if not keywords:
            return "需要搜索关键词（如「咖啡店」）"
        params = {"keywords": keywords, "key": AMAP_KEY, "offset": 5}
        if city:
            params["city"] = city
        resp = requests.get(f"{BASE}/v3/place/text", params=params, timeout=10)
        data = resp.json()
        pois = data.get("pois") or []
        if not pois:
            return f"没有搜到「{keywords}」"
        lines = [f"「{keywords}」搜索结果（前 {len(pois)} 个）："]
        for i, p in enumerate(pois, 1):
            name = p.get("name", "")
            addr = p.get("address", "")
            loc = p.get("location", "")
            line = f"{i}. {name}"
            if addr:
                line += f"（{addr}）"
            if loc:
                line += f" · {loc}"
            lines.append(line)
        return "\n".join(lines)


tool = AmapTool()  # 供 PluginManager.load_module 自动注册
