"""作息自适应（借鉴「越用越像你」思想）：根据用户在休息期的打扰频率，
动态调整 Agent 的作息，让作息逐渐向用户靠拢。

- 默认晚安 22 点（sleep_hour=22），固定睡眠 8 小时
- 醒来时间 wake_hour = (sleep_hour + 8) % 24（晚睡晚起）
- 睡眠窗口内（sleep_hour ~ wake_hour）被打扰 = 睡眠不足 8 小时，迷糊状态
- 每晚进入休息时统计：最近 3 天里 ≥2 天被打扰，就把晚安时间推迟到
  用户最晚活跃时间前 1 小时（最多推迟到 23 点）
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("friend-agent")

DEFAULT_PATH = "data/rhythm.json"
DEFAULT_SLEEP_HOUR = 22
SLEEP_DURATION = 8       # 固定睡眠 8 小时
MAX_SLEEP_HOUR = 23      # 晚安最晚 23 点
RECENT_DAYS = 3          # 统计最近几天
MIN_DISTURB_DAYS = 2     # 至少几天被打扰才算习惯


class RhythmTracker:
    """作息状态：晚安时间 + 打扰记录 + 自适应调整。"""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sleep_hour: int = DEFAULT_SLEEP_HOUR
        self.late_events: List[Dict] = []  # [{"date": "...", "hour": 23}]
        self._load()

    @property
    def wake_hour(self) -> int:
        """醒来时间 = 晚安时间 + 8 小时睡眠（晚睡晚起）。"""
        return (self.sleep_hour + SLEEP_DURATION) % 24

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.sleep_hour = int(data.get("sleep_hour", DEFAULT_SLEEP_HOUR))
                self.late_events = data.get("late_events", [])
            except Exception as exc:
                logger.warning("作息文件解析失败: %s", exc)

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"sleep_hour": self.sleep_hour,
                        "late_events": self.late_events},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 查询 ----
    def is_resting(self, hour: int) -> bool:
        """是否在睡眠窗口内（晚安后 / 醒来前）。"""
        return hour >= self.sleep_hour or hour < self.wake_hour

    def sleep_progress(self, hour: int) -> float:
        """返回已睡小时数（0-8），用于判断「睡眠不足」程度。"""
        if not self.is_resting(hour):
            return float(SLEEP_DURATION)  # 已醒来，睡满
        if hour >= self.sleep_hour:
            return float(hour - self.sleep_hour)
        # 跨午夜：22 点睡，凌晨 2 点 → 已睡 4 小时
        return float((hour + 24) - self.sleep_hour)

    # ---- 记录 ----
    def note_activity(self, hour: int) -> None:
        """用户发消息时调用：若在睡眠窗口内被打扰，记录一次。"""
        if not self.is_resting(hour):
            return
        today = date.today().isoformat()
        self.late_events.append({"date": today, "hour": hour})
        cutoff = date.today()
        self.late_events = [e for e in self.late_events
                            if (cutoff - date.fromisoformat(e["date"])).days <= 7]
        self.save()

    # ---- 自适应 ----
    def _late_hour(self, hour: int) -> int:
        """把凌晨小时归一化到 24 之后，便于比较「多晚」（0→24，1→25…）。"""
        return hour + 24 if hour < self.wake_hour else hour

    def adjust(self) -> bool:
        """每晚进入休息时调用：根据最近深夜打扰，决定是否推迟晚安时间。

        返回 True 表示作息发生变化。
        """
        today = date.today()
        recent = [e for e in self.late_events
                  if (today - date.fromisoformat(e["date"])).days <= RECENT_DAYS]
        disturb_days = {e["date"] for e in recent}
        if len(disturb_days) < MIN_DISTURB_DAYS:
            return False
        latest = max(self._late_hour(e["hour"]) for e in recent)
        # 晚安时间设在用户最晚活跃前 1 小时，但不超过 23 点、只推迟不提前
        new_sleep = max(min(latest - 1, MAX_SLEEP_HOUR), self.sleep_hour)
        if new_sleep > self.sleep_hour:
            old = self.sleep_hour
            self.sleep_hour = new_sleep
            self.save()
            logger.info("作息自适应：晚安 %d 点 → %d 点（醒来 %d 点）",
                        old, new_sleep, self.wake_hour)
            return True
        return False
