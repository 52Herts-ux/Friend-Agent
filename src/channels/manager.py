"""消息渠道管理：微信 / Telegram 等扩展能力，通过 /message 命令配置。

Friend Agent 本质是 TUI 项目，消息收发能力是「扩展渠道」：
- 微信、Telegram 等都是可插拔的渠道，通过 `/message setup <name>` 登录/配置
- `/message disable <name>` 停用，`/message` 查看状态
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

logger = logging.getLogger("friend-agent")


class MessageChannelManager:
    """管理扩展消息渠道（不含 TUI 主界面）。"""

    def __init__(self):
        self._channels: Dict[str, object] = {}   # name -> Channel
        self._enabled: set = set()               # 已启用的渠道名
        self._handle = None                      # 消息处理回调 async (user_id, text) -> str
        self._stream_handle = None               # 流式回调 async (user_id, text) -> AsyncIterator[str]
        self._tasks: Dict[str, asyncio.Task] = {}

    # ---- 注册与配置 ----
    def register(self, name: str, channel) -> None:
        """注册一个渠道（如 wechat / telegram）。"""
        self._channels[name] = channel

    def set_handler(self, handle) -> None:
        """设置消息处理回调，供渠道启动时使用。"""
        self._handle = handle

    def set_stream_handler(self, handle_stream) -> None:
        """设置流式消息回调（逐 chunk 产出，渠道可边生成边发送）。"""
        self._stream_handle = handle_stream

    def get(self, name: str):
        return self._channels.get(name)

    # ---- 启用 / 停用 ----
    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def enable(self, name: str) -> None:
        self._enabled.add(name)

    def disable(self, name: str) -> None:
        self._enabled.discard(name)

    async def start_channel(self, name: str) -> None:
        """启动一个渠道的后台收发任务（需已设置 handler）。"""
        ch = self._channels.get(name)
        if ch is None or name in self._tasks:
            return
        if self._handle is None:
            logger.warning("渠道 %s 无消息处理回调，无法启动", name)
            return
        self.enable(name)
        task = asyncio.create_task(ch.start(self._handle, self._stream_handle))
        self._tasks[name] = task
        logger.info("渠道 %s 已启用", name)

    async def stop_channel(self, name: str) -> None:
        """停用一个渠道。"""
        task = self._tasks.pop(name, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.disable(name)
        logger.info("渠道 %s 已停用", name)

    # ---- 查询 ----
    def active_channels(self):
        """返回已启用的渠道实例列表（用于广播主动推送）。"""
        return [ch for name, ch in self._channels.items() if name in self._enabled]

    def status(self) -> Dict[str, bool]:
        """返回 {渠道名: 是否启用}。"""
        return {name: (name in self._enabled) for name in self._channels}

    def names(self) -> List[str]:
        return list(self._channels.keys())
