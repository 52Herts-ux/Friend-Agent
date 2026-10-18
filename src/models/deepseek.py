"""付费大脑：DeepSeek（deepseek-flash，OpenAI 兼容）。"""
from __future__ import annotations

from .base import OpenAICompatModel, _env


class DeepSeekClient(OpenAICompatModel):
    def __init__(self, usage_tracker=None):
        super().__init__(
            name="deepseek",
            api_key=_env("DEEPSEEK_API_KEY"),
            base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=_env("DEEPSEEK_MODEL", "deepseek-flash"),
            usage_tracker=usage_tracker,
        )
