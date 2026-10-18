"""生成 HTML 小游戏工具：调用大脑模型生成单文件 HTML 游戏，保存到 data/games/。

需要 router + limiter，由 main.py 显式注册
（`plugins.register_tool(MakeGameTool(router, limiter))`）。
落地一期「后台游戏设想」为可玩成品。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..base import Tool


class MakeGameTool(Tool):
    name = "make_game"
    description = (
        "生成一个可玩的单文件 HTML 小游戏（纯 HTML+CSS+JS，无外部依赖），"
        "保存到本地并返回文件路径。当用户说「做个游戏」「写个小游戏」时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "theme": {
                "type": "string",
                "description": "游戏主题/玩法描述，如「贪吃蛇」「打地鼠」「2048」"
            }
        },
        "required": ["theme"],
    }

    def __init__(self, router, limiter):
        self.router = router
        self.limiter = limiter

    async def run(self, theme: str = "") -> str:
        if not theme.strip():
            return "游戏主题不能为空"
        try:
            _, model = self.router.route("生成游戏")
            await self.limiter.acquire("deepseek")
            raw = await model.chat(
                [{"role": "user", "content":
                  f"生成一个完整的单文件 HTML 小游戏：{theme.strip()}。"
                  "要求：纯 HTML+CSS+JS，无外部依赖，代码完整、可直接在浏览器运行、"
                  "带简单交互与计分。直接输出完整 HTML 代码。"}],
                temperature=0.8)
            code = self._extract_html(raw)
            if not code:
                return "生成失败：模型没有返回有效 HTML"
            slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", theme.strip())[:20] or "game"
            path = Path("data/games") / f"{slug}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")
            return f"✅ 已生成游戏「{theme.strip()}」：{path}（浏览器打开即玩）"
        except Exception as exc:
            return f"游戏生成失败: {exc}"

    @staticmethod
    def _extract_html(raw: str) -> str:
        """从模型输出里提取 HTML 代码（优先代码块，其次 DOCTYPE 区间）。"""
        if not raw:
            return ""
        m = re.search(r"```(?:html)?\s*(<!DOCTYPE[\s\S]*?</html>)", raw, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r"(<!DOCTYPE[\s\S]*?</html>)", raw, re.I)
        if m:
            return m.group(1).strip()
        if "<html" in raw.lower():
            return raw.strip()
        return ""
