"""目标跟踪（对齐 Muse Goals：长期目标 + 截止日期 + 进展）。

与 reminders.py 的一次性提醒不同：目标是跨几天到几周的事，
例如“考 SCSP-S”“找渗透测试工作”，需要持续跟进进展。
数据持久化在 data/goals.json。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("friend-agent")

DEFAULT_PATH = "data/goals.json"


class GoalStore:
    """本地 JSON 持久化的目标库。"""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception as exc:
                logger.warning("目标文件解析失败: %s", exc)
        return []

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._items, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def create(self, title: str, description: str = "",
               deadline: str = "", progress: int = 0) -> Dict:
        """创建目标。deadline 为 'YYYY-MM-DD' 或空；progress 为初始进度 0-100。"""
        gid = f"g{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        item = {
            "id": gid,
            "title": title.strip(),
            "description": (description or "").strip(),
            "deadline": (deadline or "").strip(),
            "status": "active",  # active / completed / cancelled
            "progress": max(0, min(100, int(progress or 0))),  # 0-100
            "created_at": now,
            "updated_at": now,
        }
        self._items.append(item)
        self.save()
        logger.info("已创建目标 %s: %s", gid, title)
        return item

    def list(self, status: str = "active") -> List[Dict]:
        """列出目标。status 为 active / completed / all。"""
        if status == "all":
            return list(self._items)
        return [i for i in self._items if i.get("status") == status]

    def get(self, gid: str) -> Optional[Dict]:
        for i in self._items:
            if i.get("id") == gid:
                return i
        return None

    def update(self, gid: str, progress: Optional[int] = None,
               status: Optional[str] = None,
               description: Optional[str] = None) -> Optional[Dict]:
        item = self.get(gid)
        if not item:
            return None
        if progress is not None:
            item["progress"] = max(0, min(100, int(progress)))
        if status in ("active", "completed", "cancelled"):
            item["status"] = status
            if status == "completed":
                item["progress"] = 100
        if description is not None:
            item["description"] = description
        item["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return item

    def due_soon(self, days: int = 3) -> List[Dict]:
        """返回 3 天内到期或已逾期的进行中目标。"""
        out = []
        today = date.today()
        for i in self._items:
            if i.get("status") != "active":
                continue
            dl = (i.get("deadline") or "").strip()
            if not dl:
                continue
            try:
                d = datetime.strptime(dl, "%Y-%m-%d").date()
            except ValueError:
                continue
            delta = (d - today).days
            if -1 <= delta <= days:
                out.append({**i, "days_left": delta})
        return out


_default_store: GoalStore | None = None


def default_goal_store() -> GoalStore:
    """模块级单例，供 goal 工具与后台引擎共享。"""
    global _default_store
    if _default_store is None:
        _default_store = GoalStore(DEFAULT_PATH)
    return _default_store
