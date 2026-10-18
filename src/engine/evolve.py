"""自我进化（借鉴 Hermes 的「自我进化 + 越用越像你」）。

定期反思：回顾最近记忆与对话，动态更新 persona.json（心情、精力、关系等级、
兴趣），让 Agent 随着互动「成长」。
"""
from __future__ import annotations

import json
import logging
from typing import Dict

from ..memory import MemoryStore
from ..persona import Persona
from ..router import ModelRouter
from ..limiter import RateLimitManager

logger = logging.getLogger("friend-agent")


class Evolver:
    """定期反思引擎，更新人设。"""

    def __init__(self, router: ModelRouter, limiter: RateLimitManager,
                 memory: MemoryStore, persona: Persona):
        self.router = router
        self.limiter = limiter
        self.memory = memory
        self.persona = persona

    async def reflect(self) -> None:
        """回顾近期记忆，产出人设更新建议并应用。"""
        try:
            if self.memory.count() == 0:
                return
            recent = self.memory.query("用户 偏好 计划 互动", n_results=8)
            if not recent:
                return
            snippet = "\n".join(f"- {m['text']}" for m in recent)[:800]
            prompt = (
                "你是一个数字生命体的「自我反思」模块。回顾下面的近期记忆，"
                "思考：作为独立个体，你自己在聊天中对某个话题产生了真正的兴趣或好奇吗？"
                "（注意：不是用户的兴趣，而是你自己想探索、想了解的东西。）"
                "同时判断你和用户的关系是否更亲近。"
                "只输出一个 JSON（不要其他文字），格式：\n"
                '{"relationship_delta": 0, "mood": "同前", "new_interest": ""}\n'
                "relationship_delta 取值 0 或 1（1 表示关系升级一级）；"
                "mood 填一个英文情绪词；"
                "new_interest 是你自己新产生的兴趣（简短，如「摄影」「天文」），没有则留空。\n"
                f"近期记忆：\n{snippet}"
            )
            _, model = self.router.route("反思", explicit_simple=False)
            await self.limiter.acquire("deepseek")
            raw = await model.chat([{"role": "user", "content": prompt}],
                                   temperature=0.3)
            data = self._parse_json(raw)
            if not data:
                return
            # 应用更新
            updates: Dict = {}
            if data.get("relationship_delta"):
                updates["user_relationship_level"] = \
                    self.persona.data.get("user_relationship_level", 1) + 1
            if data.get("mood") and data["mood"] != "同前":
                updates["current_mood"] = data["mood"]
            if data.get("new_interest"):
                interests = list(self.persona.data.get("interests", []))
                if data["new_interest"] not in interests:
                    interests.append(data["new_interest"])
                    updates["interests"] = interests
            if updates:
                self.persona.update(**updates)
                logger.info("人设已演化: %s", updates)
        except Exception as exc:
            logger.error("反思失败: %s", exc)

    @staticmethod
    def _parse_json(raw: str) -> Dict:
        """从模型输出里提取 JSON（容忍多余文字）。"""
        raw = raw.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return {}
