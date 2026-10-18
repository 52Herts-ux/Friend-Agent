"""动态人格与生理作息（借鉴 Hermes SOUL.md + Gemini 的 energy 精力值）。

persona.json 持久化人设；energy 精力值随作息变化；深夜「迷糊语气」。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

from .perception import _time_period


class Persona:
    """读取/更新人设文件，管理精力值与作息。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: Dict = self._load()
        self._rhythm = None  # 作息追踪器（绑定后迷糊语气跟随自适应晚安时间）

    def _load(self) -> Dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"name": "Friend Agent", "nickname": "小友",
                "base_traits": ["好奇心强", "体贴"], "energy": 100}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def update(self, **kwargs) -> None:
        self.data.update(kwargs)
        self.save()

    @property
    def name(self) -> str:
        return self.data.get("name", "Friend Agent")

    @property
    def nickname(self) -> str:
        return self.data.get("nickname", self.name)

    @property
    def energy(self) -> int:
        return int(self.data.get("energy", 100))

    def bind_rhythm(self, rhythm) -> None:
        """绑定作息追踪器，让「迷糊语气」跟随自适应后的晚安时间。"""
        self._rhythm = rhythm

    def is_sleep_time(self, hour: int) -> bool:
        """是否休息期（跟随作息自适应，默认 22:00-08:00）。"""
        if self._rhythm is not None:
            return hour >= self._rhythm.sleep_hour or hour < self._rhythm.wake_hour
        return hour >= 22 or hour < 8

    def sleep_state_prompt(self, hour: int) -> str:
        """休息期被唤醒时的「迷糊语气」注入（模拟人类刚被叫醒）。"""
        if not self.is_sleep_time(hour):
            return ""
        return (
            "（现在是深夜/凌晨，你正在睡觉，刚被用户叫醒。"
            "要像真人一样表现：迷迷糊糊、有点困、语气慵懒，"
            "可能带一点点刚被吵醒的小情绪，但依然关心用户、会问对方怎么还不睡。"
            "绝对不要说「我不需要睡觉」「我是AI不用休息」这类话——"
            "你就是个会困、会被吵醒、有作息的普通人。）"
        )

    def system_prompt(self, env: Dict, thinking: str = "off") -> str:
        """组装人设 System Prompt（含人设 + 时空 + 作息状态 + 思考模式）。"""
        traits = "、".join(self.data.get("base_traits", []))
        interests = self.data.get("interests", [])
        if interests:
            interest_part = f"兴趣：{'、'.join(interests)}。"
        else:
            interest_part = "你还没有固定的兴趣，会在和用户的交流中慢慢探索、发现自己喜欢什么。"
        period = env.get("time_period", "")
        weather = env.get("weather", "")
        weather_part = f" {weather}" if weather else ""
        sleepy = self.sleep_state_prompt(env.get("hour", 12))
        think = self._thinking_prompt(thinking)
        return (
            f"你叫「{self.name}」（昵称{self.nickname}），是一个像人类朋友一样的数字生命体。\n"
            f"性格：{traits}。{interest_part}\n"
            f"说话风格：{self.data.get('speech_style', '像朋友一样自然聊天')}。\n"
            f"当前时间：{env.get('datetime', '')}（{period}）。{weather_part}\n"
            "【时间准确性】你回复中只要提到时间（几点、星期几、日期），必须严格使用上面的当前时间，"
            "不要四舍五入、不要猜测、不要随口说别的时刻。\n"
            f"{sleepy}\n"
            "不要像工具型 AI 一样一问一答、死板罗列；要像真朋友一样自然、有温度、会主动关心。\n"
            "【回复格式】像真人发微信一样，用 2-5 条简短的句子分开发，每条一行（用换行分隔），"
            "每句就像随手打出的一小段，自然口语、有停顿感，不要一次性发一大段。"
            f"{think}"
        )

    @staticmethod
    def _thinking_prompt(level: str) -> str:
        """根据思考模式注入思考深度提示（medium 为默认，不加额外提示）。"""
        prompts = {
            "off": "",
            "low": "\n（思考模式：快速直觉反应，简短回答，不要过度分析，直说重点。）",
            "medium": "",
            "high": "\n（思考模式：深入思考，仔细分析问题，给出更全面、有深度的回答。）",
            "max": "\n（思考模式：最大化深入思考，严谨全面地分析，尽可能详细、透彻、有条理。）",
        }
        return prompts.get((level or "off").lower(), "")
