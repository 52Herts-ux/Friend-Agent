"""内置工具示例：天气查询（演示 Tool 插件怎么用）。"""
from __future__ import annotations

import requests

from ..base import Tool


class WeatherTool(Tool):
    name = "get_weather"
    description = "查询指定城市的实时天气（温度、天气状况）。当用户询问天气时使用。"
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名（拼音或英文，如 Beijing / Shanghai）"}
        },
        "required": ["city"],
    }

    async def run(self, city: str = "Beijing") -> str:
        try:
            resp = requests.get(f"https://wttr.in/{city}?format=%c+%t+%h", timeout=8)
            return f"{city} 天气：{resp.text.strip()}"
        except Exception as exc:
            return f"天气查询失败: {exc}"


tool = WeatherTool()  # 供 PluginManager.load_module 自动注册
