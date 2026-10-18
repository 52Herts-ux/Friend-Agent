"""日程提醒工具：把提醒存入 ReminderStore，到点由后台引擎推送。

时间解析在工具内部完成（不依赖 LLM 换算），支持自然语言：
- 「30分钟后」「2小时后」
- 「今天下午3点」「明天8点」「后天晚上9点」「明天下午3点半」
- 「2026-10-08 15:00」（绝对格式）
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..base import Tool
from ...reminders import default_store


def parse_time(text: str, now: datetime) -> datetime:
    """把自然语言时间解析成 datetime（本地时区）。解析失败抛 ValueError。"""
    text = (text or "").strip()
    # 1. 绝对格式
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # 2. X分钟后 / X小时后
    m = re.search(r"(\d+)\s*分钟后", text)
    if m:
        return now + timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*小时后", text)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # 3. 日期偏移（今天/明天/后天）
    day = 0
    if "后天" in text:
        day = 2
    elif "明天" in text or "明日" in text:
        day = 1

    # 4. 提取小时和分钟
    m = re.search(r"(\d{1,2})\s*[点:：]", text)
    if not m:
        raise ValueError(f"无法解析时间「{text}」")
    hour = int(m.group(1))
    minute = 0
    if "半" in text:
        minute = 30  # 「3点半」→ 30 分
    else:
        m2 = re.search(r"[点:：]\s*(\d{1,2})\s*分?", text)
        if m2:
            minute = int(m2.group(1))

    # 5. 时段修正（下午/晚上 → +12，中午 → 12）
    if ("下午" in text or "晚上" in text or "傍晚" in text) and hour < 12:
        hour += 12
    if "中午" in text and hour < 11:
        hour = 12

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target + timedelta(days=day)


class ScheduleReminderTool(Tool):
    name = "schedule_reminder"
    description = (
        "为用户设置一条定时提醒。当用户说「提醒我…」「X点提醒我…」「明天…提醒我」时使用。"
        "直接传用户说的自然语言时间即可，工具会自动换算（如「明天下午3点」「30分钟后」）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "remind_at": {
                "type": "string",
                "description": "提醒时间（自然语言或绝对时间），如「明天下午3点」「30分钟后」「2026-10-08 15:00」"
            },
            "content": {
                "type": "string",
                "description": "提醒内容（简洁，如「开会」「吃药」「给妈妈打电话」）"
            },
        },
        "required": ["remind_at", "content"],
    }

    def __init__(self, store=None):
        self.store = store or default_store()

    async def run(self, remind_at: str = "", content: str = "") -> str:
        if not remind_at or not content:
            return "参数不完整：需要 remind_at（时间）和 content（内容）"
        now = datetime.now()
        try:
            target = parse_time(remind_at, now)
        except ValueError as exc:
            return f"时间解析失败「{remind_at}」：{exc}。请用「明天下午3点」「30分钟后」或「2026-10-08 15:00」"
        if target <= now:
            return (f"时间「{remind_at}」已过去（解析为 {target.strftime('%Y-%m-%d %H:%M')}），"
                    "请确认是否为未来时间")
        remind_str = target.strftime("%Y-%m-%d %H:%M")
        self.store.add(remind_str, content.strip())
        return f"✅ 已设置提醒：{remind_str} — {content.strip()}"


tool = ScheduleReminderTool()  # 供 PluginManager.load_module 自动注册
