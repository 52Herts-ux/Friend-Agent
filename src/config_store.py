"""配置存储 + 模型提供商定义（借鉴 Pi 的 provider 列表 + /login 凭据保存）。

/ login 交互：方向键选 provider → 输入 API key → 写回 .env。
"""
from __future__ import annotations

import os
from typing import Dict, List

from dotenv import dotenv_values, load_dotenv, set_key

DOTENV_PATH = "config/.env"

# 模型提供商定义（登录 UI 与模型初始化共用）
PROVIDERS: List[Dict] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "desc": "付费大脑（deepseek-flash，复杂对话/创作）",
        "key_var": "DEEPSEEK_API_KEY",
        "base_url_var": "DEEPSEEK_BASE_URL",
        "model_var": "DEEPSEEK_MODEL",
        "needs_key": True,
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
    },
    {
        "id": "agnes",
        "name": "Agnes",
        "desc": "省钱层（agnes-2.5-flash，免费，简单操作）",
        "key_var": "AGNES_API_KEY",
        "base_url_var": "AGNES_BASE_URL",
        "model_var": "AGNES_MODEL",
        "needs_key": True,
        "default_base_url": "https://api.agnes-ai.com/v1",
        "default_model": "agnes-2.5-flash",
    },
    {
        "id": "ollama",
        "name": "Ollama（本地）",
        "desc": "本地小脑（零成本，无需 key）",
        "needs_key": False,
    },
]

PROVIDER_BY_ID = {p["id"]: p for p in PROVIDERS}


class ConfigStore:
    """读写 .env 配置。"""

    def __init__(self, path: str = DOTENV_PATH):
        self.path = path

    def load(self) -> None:
        load_dotenv(self.path)

    def get(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default).strip()

    def values(self) -> Dict[str, str]:
        return dotenv_values(self.path)

    def set(self, key: str, value: str) -> None:
        """写回 .env（追加或更新），并同步到当前进程环境。"""
        set_key(self.path, key, value)
        os.environ[key] = value

    def has_key(self, key_var: str) -> bool:
        return bool(self.get(key_var))

    def configured_providers(self) -> List[str]:
        """返回已配置（有 key 或无需 key）的 provider id 列表。"""
        out = []
        for p in PROVIDERS:
            if not p.get("needs_key") or self.has_key(p["key_var"]):
                out.append(p["id"])
        return out
