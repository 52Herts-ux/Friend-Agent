"""记笔记工具：把重要信息写入长期记忆（MemoryStore）。

需要 MemoryStore 实例，由 main.py 显式注册（`plugins.register_tool(NoteTool(memory))`）。
"""
from __future__ import annotations

from ..base import Tool


class NoteTool(Tool):
    name = "save_note"
    description = (
        "把重要信息存入长期记忆。当用户说「记住…」「记下…」或表达"
        "重要的偏好、计划、事实时使用，之后聊天时会自动回忆起。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "要记住的内容（简洁、具体，如「老大喜欢喝冰美式」）"
            },
            "category": {
                "type": "string",
                "description": "分类：user_preference / plan / fact / note，默认 note"
            },
        },
        "required": ["content"],
    }

    def __init__(self, memory):
        self.memory = memory

    async def run(self, content: str = "", category: str = "note") -> str:
        if not content.strip():
            return "内容为空，没记下什么"
        self.memory.store(content.strip(), category=category, importance=2)
        return f"✅ 已记住：{content.strip()}"


class ClearMemoryTool(Tool):
    name = "clear_memory"
    description = (
        "清空全部长期记忆。当用户说「清理记忆」「清空记忆」「忘记一切」「重置记忆」"
        "「删除记忆」时使用，会彻底删除已存的所有记忆。"
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, memory):
        self.memory = memory

    async def run(self, **kwargs) -> str:
        removed = self.memory.clear()
        remaining = self.memory.count()
        return f"✅ 已清空记忆（删除 {removed} 条，剩余 {remaining} 条）"
