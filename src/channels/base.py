"""渠道抽象层（借鉴 Pi 多渠道 + OpenClaw channels 思想）。

Friend Agent 核心逻辑（大脑/记忆/潜意识）与通信渠道解耦：
- 微信、TUI、未来 Telegram/Web UI 都实现 Channel 接口
- 新增渠道 = 新增一个 adapter，核心零改动
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Awaitable, Callable

# 收到用户消息的回调签名：async (user_id: str, text: str) -> str（返回完整回复）
MessageHandler = Callable[[str, str], Awaitable[str]]

# 流式回调：async (user_id, text) -> AsyncIterator[str]（逐 chunk 产出回复）
StreamMessageHandler = Callable[[str, str], AsyncIterator[str]]


class Channel(ABC):
    """通信渠道抽象接口。"""

    name: str = "base"

    @abstractmethod
    async def start(self, handler: MessageHandler) -> None:
        """启动渠道，收到用户消息时调用 handler 并回复。"""

    @abstractmethod
    async def send(self, user_id: str, text: str) -> None:
        """主动发送消息给用户（用于主动推送/关怀）。"""
