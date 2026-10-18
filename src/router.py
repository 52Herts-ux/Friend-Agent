"""模型路由：按任务难度决定走「大脑 / 省钱层 / 小脑」（借鉴 Pi 跨 provider 切换）。

- deepseek（大脑）：复杂对话、情感分析、深度创作、高情商关怀
- agnes（省钱层）：简单操作、信息初筛、格式化、是/否判断
- ollama（小脑）：后台潜意识、记忆整理、垃圾过滤（零成本）

MVP 阶段用启发式分类：长/带创作关键词/带情感诉求 → 大脑；其余默认大脑，
明确标记 `simple=True` 的任务走 agnes。后续可让 Ollama 做难度预判。
"""
from __future__ import annotations

from typing import Optional

_SIMPLE_HINTS = ("是", "否", "对不对", "翻译", "总结", "缩写", "列出", "格式化", "判断")
_COMPLEX_HINTS = ("设计", "写代码", "写个", "帮我写", "分析", "建议", "安慰", "心情", "难过", "开心", "怎么办")

# 降级顺序：首选未配置时按此顺序找第一个已配置的模型
_FALLBACK_ORDER = ["deepseek", "agnes", "ollama"]


def classify(task: str, *, explicit_simple: bool = False) -> str:
    """返回模型名：'deepseek' | 'agnes' | 'ollama'。"""
    if explicit_simple:
        return "agnes"
    # 明显的简单指令 → 省钱层
    if any(h in task for h in _SIMPLE_HINTS) and len(task) < 30:
        return "agnes"
    return "deepseek"


class ModelRouter:
    """持有三个模型客户端，按任务路由。"""

    def __init__(self, deepseek, agnes, ollama):
        self._models = {"deepseek": deepseek, "agnes": agnes, "ollama": ollama}

    def get(self, name: str):
        return self._models[name]

    def route(self, task: str, *, explicit_simple: bool = False):
        """返回 (model_name, client)。首选未配置时自动降级到已配置模型。"""
        name = classify(task, explicit_simple=explicit_simple)
        client = self._models[name]
        if not getattr(client, "is_configured", True):
            for alt in _FALLBACK_ORDER:
                cand = self._models.get(alt)
                if cand is not None and getattr(cand, "is_configured", True):
                    return alt, cand
        return name, client

    def all(self):
        return self._models
