"""插件协议（借鉴 Pi 的 tools/extensions 扩展机制，Python 轻量版）。

后续扩展能力（多模态、新工具、新渠道…）只需实现对应接口并注册，无需改核心。
提供两类核心插件：

- Tool：可被 Agent 通过 function calling 调用的能力（Agent 的「手」）
- Hook：生命周期钩子（消息前/后、回合结束等挂载点）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class Tool(ABC):
    """工具插件：一个可被 Agent 调用的能力。"""

    name: str = ""                 # 函数名（唯一）
    description: str = ""          # 给 LLM 看的能力描述
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """执行工具，返回文本结果。"""

    def openai_schema(self) -> Dict[str, Any]:
        """转成 OpenAI function calling 的工具描述。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Hook(ABC):
    """钩子插件：挂载到对话生命周期。"""

    name: str = ""

    async def on_user_message(self, user_id: str, text: str) -> None:
        """收到用户消息时（处理前）。"""

    async def on_assistant_reply(self, user_id: str, text: str, reply: str) -> None:
        """Agent 回复后。"""

    async def on_turn_end(self, user_id: str) -> None:
        """一个回合结束。"""
