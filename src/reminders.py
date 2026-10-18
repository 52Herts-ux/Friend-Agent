"""日程提醒：本地 JSON 持久化 + 到点检查（data/reminders.json）。

模块级单例 `default_store()` 供 schedule 工具与后台引擎共享同一实例，
避免两个实例各自持有内存副本、互不可见。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("friend-agent")

DEFAULT_PATH = "data/reminders.json"


class ReminderStore:
    """本地 JSON 持久化的日程提醒。"""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("提醒文件解析失败: %s", exc)
        return []

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def add(self, remind_at: str, content: str) -> str:
        """添加提醒。remind_at 为 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD HH:MM:SS'。"""
        rid = f"r{int(time.time() * 1000)}"
        self._items.append({
            "id": rid,
            "remind_at": remind_at,
            "content": content,
            "done": False,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.save()
        logger.info("已添加提醒 %s @ %s: %s", rid, remind_at, content)
        return rid

    def _parse(self, s: str) -> datetime:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"无法解析时间: {s}")

    def due(self, now: datetime, window_seconds: int = 360) -> List[Dict]:
        """返回已到点且未完成的提醒（0 <= now - remind_at <= window）。"""
        out = []
        for item in self._items:
            if item.get("done"):
                continue
            try:
                at = self._parse(item["remind_at"])
            except ValueError:
                continue
            delta = (now - at).total_seconds()
            if 0 <= delta <= window_seconds:
                out.append(item)
        return out

    def mark_done(self, rid: str) -> None:
        for item in self._items:
            if item["id"] == rid:
                item["done"] = True
        self.save()

    def pending_count(self) -> int:
        return sum(1 for i in self._items if not i.get("done"))


_default_store: ReminderStore | None = None


def default_store() -> ReminderStore:
    """模块级单例，schedule 工具与后台引擎共享。"""
    global _default_store
    if _default_store is None:
        _default_store = ReminderStore(DEFAULT_PATH)
    return _default_store
