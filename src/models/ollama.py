"""本地小脑：Ollama（qwen2.5:3b，零成本，OpenAI 兼容端点）。"""
from __future__ import annotations

from .base import OpenAICompatModel, _env


class OllamaClient(OpenAICompatModel):
    def __init__(self, usage_tracker=None):
        super().__init__(
            name="ollama",
            api_key="ollama",  # Ollama 本地不校验，占位即可
            base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1",
            model=_env("OLLAMA_MODEL", "qwen2.5:3b"),
            usage_tracker=usage_tracker,
        )
