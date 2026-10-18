"""上下文组装（借鉴 Pi `transformContext → convertToLlm` 消息流）。

把「人设 + 时空 + 相关记忆 + 对话历史」组装成 LLM 可用的 messages 列表。
"""
from __future__ import annotations

from typing import Dict, List

MAX_HISTORY = 20  # 对话历史最多保留的条数（短期记忆）


def build_messages(persona, env: Dict, memory, user_input: str,
                   history: List[Dict], thinking: str = "off") -> List[Dict]:
    """组装完整 messages。"""
    system = persona.system_prompt(env, thinking=thinking)
    recall = memory.recall_as_context(user_input) if memory else ""
    if recall:
        system += "\n\n" + recall

    messages: List[Dict] = [{"role": "system", "content": system}]
    messages.extend(history[-MAX_HISTORY:])
    messages.append({"role": "user", "content": user_input})
    return messages


def trim_history(history: List[Dict], max_len: int = MAX_HISTORY) -> List[Dict]:
    return history[-max_len:]
