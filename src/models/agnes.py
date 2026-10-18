"""省钱层：Agnes-2.5-Flash（免费模型，做简单操作，OpenAI 兼容）。"""
from __future__ import annotations

from .base import OpenAICompatModel, _env


class AgnesClient(OpenAICompatModel):
    def __init__(self, usage_tracker=None):
        super().__init__(
            name="agnes",
            api_key=_env("AGNES_API_KEY"),
            base_url=_env("AGNES_BASE_URL", "https://api.agnes-ai.com/v1"),
            model=_env("AGNES_MODEL", "agnes-2.5-flash"),
            usage_tracker=usage_tracker,
        )
