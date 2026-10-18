"""模型层抽象（借鉴 Pi `pi-ai` 的 provider 抽象）。

DeepSeek / Agnes / Ollama 三者均提供 OpenAI 兼容接口，统一用 `AsyncOpenAI`
适配，`ModelClient` 是统一对话接口，方便 `router.py` 按任务难度切换模型。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI


class ModelClient(ABC):
    """统一模型客户端接口（对应 Pi 的 getModel + streamSimple）。"""

    name: str = "base"

    @abstractmethod
    async def chat(self, messages: list[dict], temperature: float = 0.7,
                   max_tokens: Optional[int] = None) -> str:
        """非流式对话，返回完整文本。"""

    @abstractmethod
    async def chat_stream(self, messages: list[dict], temperature: float = 0.7,
                          max_tokens: Optional[int] = None) -> AsyncIterator[str]:
        """流式对话，逐段产出文本。"""


class OpenAICompatModel(ModelClient):
    """OpenAI 兼容模型通用实现（DeepSeek / Agnes / Ollama 共用）。

    未配置 API key 时不创建客户端（容错，保证能先进 TUI 用 /login 配置），
    真正调用时才报友好提示。
    """

    def __init__(self, name: str, api_key: str, base_url: str, model: str,
                 usage_tracker=None):
        self.name = name
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._usage_tracker = usage_tracker
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None

    def _record_usage(self, usage) -> None:
        """把响应里的 usage 记入统计器（无 tracker 或 usage 则跳过）。"""
        if self._usage_tracker is None or usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        if prompt or completion:
            self._usage_tracker.record(self.name, prompt, completion)

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def _require_client(self):
        if self._client is None:
            raise RuntimeError(
                f"模型 [{self.name}] 未配置 API key，请在 TUI 里输入 /login 配置，或手动编辑 config/.env")
        return self._client

    async def chat(self, messages: list[dict], temperature: float = 0.7,
                   max_tokens: Optional[int] = None) -> str:
        client = self._require_client()
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = await client.chat.completions.create(**kwargs)
        self._record_usage(getattr(resp, "usage", None))
        return resp.choices[0].message.content or ""

    async def chat_stream(self, messages: list[dict], temperature: float = 0.7,
                          max_tokens: Optional[int] = None) -> AsyncIterator[str]:
        client = self._require_client()
        kwargs: dict = {"model": self.model, "messages": messages,
                        "temperature": temperature, "stream": True}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        # 让 provider 在最后一个 chunk 里附带 usage（DeepSeek/OpenAI 兼容端点均支持）
        kwargs["stream_options"] = {"include_usage": True}
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                self._record_usage(chunk.usage)
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_with_tools(self, messages: list[dict], tools: Optional[list] = None,
                              temperature: float = 0.7):
        """带工具调用（function calling），返回 (content, tool_calls)。

        tool_calls 为 OpenAI 格式的列表（可为 None）。
        """
        client = self._require_client()
        kwargs: dict = {"model": self.model, "messages": messages,
                        "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        resp = await client.chat.completions.create(**kwargs)
        self._record_usage(getattr(resp, "usage", None))
        msg = resp.choices[0].message
        return msg.content or "", msg.tool_calls

    async def chat_stream_with_tools(self, messages: list[dict], tools: Optional[list] = None,
                                     temperature: float = 0.7,
                                     max_tokens: Optional[int] = None):
        """流式对话 + 工具调用。yield ('text', str) 或 ('tool_calls', list)。

        模型要么流式产出文本（无工具调用），要么产出 tool_calls（需执行工具）。
        始终把 tools 传给模型，让 LLM 自己决定是否调用（对齐 Pi/Hermes 的做法）。
        """
        client = self._require_client()
        kwargs: dict = {"model": self.model, "messages": messages,
                        "temperature": temperature, "stream": True,
                        "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        stream = await client.chat.completions.create(**kwargs)
        tool_calls: dict = {}  # index -> {id, name, arguments}
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                self._record_usage(chunk.usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield ("text", delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    e = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        e["id"] = tc.id
                    if tc.function and tc.function.name:
                        e["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        e["arguments"] += tc.function.arguments
        if tool_calls:
            yield ("tool_calls", [tool_calls[i] for i in sorted(tool_calls)])


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()
