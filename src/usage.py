"""用量统计：累计输入/输出 token、预估费用、上下文健康度（对齐 Pi footer）。

Pi 的 footer 会实时显示：模型、上下文使用（已用/总值）、累计用量（in/out）、
预估费用。这里提供同样的数据源，供 TUI 状态栏渲染。

- 输入/输出 token：模型响应里的 usage.prompt_tokens / completion_tokens
- 上下文已用：最近一次请求的 prompt_tokens（含系统提示 + 历史 + 当前消息）
- CH（Context Health）：剩余上下文占比 = (1 - 已用/窗口) * 100%
- 费用：按各 provider 单价估算（元/百万 token）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# 各 provider 的单价（元 / 百万 token）与上下文窗口
# 价格参考 2026-10 官方标价，flash/免费层可能更便宜，可在此微调。
_PRICING: dict = {
    # provider -> (输入价, 输出价, 上下文窗口)
    "deepseek": (1.0, 2.0, 128_000),
    "agnes": (0.0, 0.0, 32_000),
    "ollama": (0.0, 0.0, 32_000),
}

_DEFAULT = (0.0, 0.0, 128_000)

# 思考模式级别（对齐 Pi：off/low/medium/high/max）
THINKING_LEVELS = ("off", "low", "medium", "high", "max")


def _k_str(v: float) -> str:
    """数值转 k 单位字符串，去掉多余的 0（34.0 -> 34，3.4 -> 3.4）。"""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def fmt_k(n: float) -> str:
    """把 token 数格式化成 k 单位：1000 -> 1k，3400 -> 3.4k，340 -> 340。"""
    n = int(n)
    if n >= 1000:
        return f"{_k_str(n / 1000)}k"
    return str(n)


def fmt_ctx(n: float) -> str:
    """上下文 token 数格式化：128000 -> 128k。"""
    return fmt_k(n)


def fmt_fee(yuan: float) -> str:
    """费用格式化：0.0234 -> ¥0.023，0.5 -> ¥0.50。"""
    if yuan < 0.0001:
        return "¥0"
    if yuan < 0.01:
        return "¥" + f"{yuan:.4f}".rstrip("0").rstrip(".")
    if yuan < 1:
        return "¥" + f"{yuan:.3f}".rstrip("0").rstrip(".")
    return f"¥{yuan:.2f}"


class UsageTracker:
    """会话级用量统计（线程安全由单线程 asyncio 保证）。"""

    def __init__(self, thinking_path: str = "data/thinking.json"):
        self.total_prompt_tokens: int = 0      # 累计输入
        self.total_completion_tokens: int = 0  # 累计输出
        self.context_tokens: int = 0           # 当前上下文已用（最近一次 prompt）
        self.current_model: str = "deepseek"   # 最近一次调用模型的 provider
        self._thinking_path = Path(thinking_path)
        self.thinking: str = self._load_thinking()  # 思考模式（持久化，默认 high）

    def _load_thinking(self) -> str:
        """加载持久化的思考模式（无文件或非法值时默认 high）。"""
        if self._thinking_path.exists():
            try:
                level = json.loads(
                    self._thinking_path.read_text(encoding="utf-8")
                ).get("thinking", "high")
                return level if level in THINKING_LEVELS else "high"
            except Exception:
                pass
        return "high"

    def _save_thinking(self) -> None:
        try:
            self._thinking_path.parent.mkdir(parents=True, exist_ok=True)
            self._thinking_path.write_text(
                json.dumps({"thinking": self.thinking}, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    # ---- 记录 ----
    def record(self, provider: str, prompt_tokens: int,
               completion_tokens: int) -> None:
        """模型每次响应后调用，累计用量并更新上下文。"""
        if prompt_tokens < 0 or completion_tokens < 0:
            return
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.context_tokens = prompt_tokens
        if provider:
            self.current_model = provider

    def set_thinking(self, level: str) -> None:
        """设置思考模式（非法值回落到 off），并持久化。"""
        level = (level or "off").lower()
        self.thinking = level if level in THINKING_LEVELS else "off"
        self._save_thinking()

    def cycle_thinking(self) -> str:
        """循环切换到下一档思考模式，返回新模式，并持久化。"""
        idx = THINKING_LEVELS.index(self.thinking) if self.thinking in THINKING_LEVELS else 0
        nxt = THINKING_LEVELS[(idx + 1) % len(THINKING_LEVELS)]
        self.thinking = nxt
        self._save_thinking()
        return nxt

    # ---- 查询 ----
    def _pricing(self, provider: Optional[str] = None):
        return _PRICING.get(provider or self.current_model, _DEFAULT)

    @property
    def context_window(self) -> int:
        return self._pricing()[2]

    @property
    def fee(self) -> float:
        """累计预估费用（元）。"""
        in_price, out_price, _ = self._pricing(self.current_model)
        return (self.total_prompt_tokens * in_price
                + self.total_completion_tokens * out_price) / 1_000_000

    @property
    def context_health(self) -> int:
        """CH：剩余上下文占比（0-100）。"""
        win = self.context_window
        if win <= 0:
            return 100
        used = min(self.context_tokens, win)
        return max(0, int(round((1 - used / win) * 100)))

    # ---- 状态栏渲染 ----
    def status_line(self) -> str:
        """返回 footer 状态栏文本（Pi 风格）。"""
        win = self.context_window
        parts = [
            f"in {fmt_k(self.total_prompt_tokens)}",
            f"out {fmt_k(self.total_completion_tokens)}",
            f"CH {self.context_health}%",
            fmt_fee(self.fee),
            f"ctx {fmt_ctx(self.context_tokens)}/{fmt_ctx(win)}",
            self.current_model,
            f"thinking:{self.thinking}",
        ]
        return " · ".join(parts)
