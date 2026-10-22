"""目标工具：让 Agent 能创建 / 查看 / 更新长期目标。

用户说“我要考 SCSP”“帮我记下找工作这个目标”“考试进度到 60% 了”
这类话时，LLM 用这个工具记下来，而不是只记一次性提醒。
"""
from __future__ import annotations

from ..base import Tool
from ...goals import default_goal_store


class GoalTool(Tool):
    name = "goal"
    description = (
        "管理长期目标（不是一次性提醒）。"
        "用户提到长期计划、考试、找工作、学某项技能时用它记下来；"
        "用户说进度、完成、放弃时用它更新。"
        "action: create=新建, list=查看, update=更新进度/状态。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "create / list / update",
            },
            "title": {
                "type": "string",
                "description": "目标标题，如“考 SCSP-S”“找渗透测试工作”",
            },
            "description": {
                "type": "string",
                "description": "目标补充说明",
            },
            "deadline": {
                "type": "string",
                "description": "截止日期 YYYY-MM-DD，可为空",
            },
            "goal_id": {
                "type": "string",
                "description": "update 时的目标 id（list 里能看到）",
            },
            "progress": {
                "type": "integer",
                "description": "进度 0-100",
            },
            "status": {
                "type": "string",
                "description": "active / completed / cancelled",
            },
        },
        "required": ["action"],
    }

    async def run(self, action: str = "list", title: str = "",
                  description: str = "", deadline: str = "",
                  goal_id: str = "", progress: int | None = None,
                  status: str = "") -> str:
        store = default_goal_store()
        action = (action or "list").strip().lower()
        if action == "create":
            if not title.strip():
                return "创建目标需要标题，请问这个目标叫什么？"
            item = store.create(title, description, deadline,
                                progress=progress or 0)
            return (f"已记下目标：{item['title']}（id: {item['id']}，进度 {item['progress']}%）"
                    + (f"，截止 {item['deadline']}" if item["deadline"] else ""))
        if action == "update":
            if not goal_id:
                return "更新目标需要 goal_id，先用 list 看一下目标列表。"
            item = store.update(goal_id, progress=progress,
                                status=status or None,
                                description=description or None)
            if not item:
                return f"没找到 id 为 {goal_id} 的目标。"
            return (f"已更新目标：{item['title']}，进度 {item['progress']}%，"
                    f"状态 {item['status']}")
        # 默认 list
        items = store.list(status="active")
        if not items:
            return "当前没有进行中的目标。"
        lines = []
        for i in items:
            dl = f"，截止 {i['deadline']}" if i.get("deadline") else ""
            lines.append(f"- {i['title']}（id: {i['id']}，进度 {i.get('progress', 0)}%{dl}）")
        return "进行中的目标：\n" + "\n".join(lines)


tool = GoalTool()  # 供 PluginManager.load_module 自动注册
