"""后台潜意识自主循环（借鉴 Gemini 的 AutonomousEngine + Hermes 的 cron）。

每 180 秒轮询一次：主动关怀（饭点/深夜）+ 基于用户偏好的自主探索，
在用户空闲时运行，通过 on_push 回调主动推送消息。

推送逻辑（改版：不再是固定冷却到点就推）：
- 欲望值 desire：只在空闲 tick 慢慢累积，涨满阈值才考虑推；
  阈值每次随机，推完/被拒后都重新随机，时间点算不出来。
- 冷却只做下限（2 小时内绝不推），不做定时器。
- 每日上限 2 条主动推送（关怀 + 探索合在一起算，用户自己设的提醒不限）。
- 探索必须有真实搜索结果才推：没接入搜索工具时直接跳过，绝不编造。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from ..memory import MemoryStore, USER_CATEGORIES
from ..persona import Persona
from ..perception import get_environment_context
from ..router import ModelRouter
from ..limiter import RateLimitManager

logger = logging.getLogger("friend-agent")

TICK_SECONDS = 180          # 每 180 秒轮询
IDLE_SECONDS = 120          # 用户空闲 2 分钟才进自主逻辑（探索内部还有更严的 30 分钟门槛）
PUSH_COOLDOWN = 6 * 3600    # 保留兼容：旧常量，不再作为唯一推送门
# --- 新推送门：欲望值 + 冷却下限 + 每日上限 ---
PUSH_FLOOR_SECONDS = 2 * 3600   # 冷却下限：上次主动推送 2 小时内绝不推
PUSH_DAILY_CAP = 2              # 每日主动推送上限（关怀+探索合算；用户自设提醒不限）
QUIET_DAY_PROB = 0.30         # 每天 30% 概率是“静默日”：当天一条主动推送都不发
PUSH_IDLE_SECONDS = 30 * 60     # 探索推送要求空闲满 30 分钟（避免刚聊完就推）
DESIRE_GAIN_MIN = 0.05          # 每空闲 tick 欲望值涨幅下限
DESIRE_GAIN_MAX = 0.15          # 每空闲 tick 欲望值涨幅上限
DESIRE_THRESHOLD_MIN = 0.7      # 欲望阈值每次随机
DESIRE_THRESHOLD_MAX = 1.0
PUSH_COIN_FLIP = 0.5            # 欲望涨满后仍有 50% 概率这次不推（制造“有时候几天都不推”）
CARE_PROB = {"morning": 0.4, "lunch": 0.3, "sleep": 0.5}  # 固定关怀每天各只掷一次骰子


class BackgroundEngine:
    """后台潜意识引擎：负责主动关怀与自主探索。"""

    def __init__(self, router: ModelRouter, limiter: RateLimitManager,
                 memory: MemoryStore, persona: Persona,
                 on_push: Callable[[str], Awaitable[None]],
                 loop=None, evolver=None, rhythm=None, plugins=None):
        self.router = router
        self.limiter = limiter
        self.memory = memory
        self.persona = persona
        self.on_push = on_push
        self.loop = loop          # AgentLoop 引用，用于判断用户是否空闲
        self.evolver = evolver    # 自我进化模块，每日定期反思
        self.rhythm = rhythm      # 作息自适应追踪器
        self.plugins = plugins    # PluginManager：探索用它调 search_web；为 None 时不编造、直接跳过
        self.last_interaction: float = time.time()
        self._reminded_path = Path("data/reminded.json")
        self._reminded: Dict[str, str] = self._load_reminded()  # 每天一次的提醒去重（持久化，重启不重复）
        self._last_reflect_day: str = ""     # 每日反思去重
        self._last_push_ts: float = 0.0      # 上次主动推送时间（冷却下限用）
        self._last_summary_day: str = self._reminded.get("summary", "")  # 睡前总结去重（持久化）
        self._last_memory_reflect_day: str = ""  # 修复：_memory_reflection 引用了它，原版 __init__ 里漏了初始化
        # --- 新推送状态 ---
        self._desire: float = 0.0
        self._desire_threshold: float = random.uniform(DESIRE_THRESHOLD_MIN, DESIRE_THRESHOLD_MAX)
        self._pushes_today: int = 0
        self._push_day: str = ""
        self._cap_today: int = PUSH_DAILY_CAP  # 当天实际上限（静默日为 0）

    def _load_reminded(self) -> Dict[str, str]:
        """加载当天已发过的关怀（重启后不重复道晚安/早安/午饭）。"""
        if self._reminded_path.exists():
            try:
                data = json.loads(self._reminded_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _mark_reminded(self, key: str, today: str) -> None:
        """记录某关怀今天已发，并持久化。"""
        self._reminded[key] = today
        try:
            self._reminded_path.parent.mkdir(parents=True, exist_ok=True)
            self._reminded_path.write_text(
                json.dumps(self._reminded, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:
            logger.warning("关怀去重持久化失败: %s", exc)

    def note_interaction(self) -> None:
        self.last_interaction = time.time()

    # ---- 新推送门：每日上限 / 记账 ----
    def _reset_daily_count(self, today: str) -> None:
        if self._push_day != today:
            self._push_day = today
            self._pushes_today = 0
            # 每天只掷一次：30% 概率今天是静默日，一条主动推送都不发
            self._cap_today = 0 if random.random() < QUIET_DAY_PROB else PUSH_DAILY_CAP
            if self._cap_today == 0:
                logger.info("今天是静默日，主动推送全部跳过")

    def _can_push_proactive(self, today: str) -> bool:
        """今天的主动推送预算还有没有（用户自设提醒不走这里，不受限）。"""
        self._reset_daily_count(today)
        return self._pushes_today < self._cap_today

    def _note_proactive_push(self, today: str) -> None:
        """一次主动推送成功后记账：欲望清零、阈值重随、冷却下限重算。"""
        self._reset_daily_count(today)
        self._pushes_today += 1
        self._last_push_ts = time.time()
        self._desire = 0.0
        self._desire_threshold = random.uniform(DESIRE_THRESHOLD_MIN, DESIRE_THRESHOLD_MAX)

    def _dampen_desire(self) -> None:
        """这次决定不推 / 内容被判不相关：欲望减半而不是清零，下次仍有机会。"""
        self._desire *= 0.5
        self._desire_threshold = random.uniform(DESIRE_THRESHOLD_MIN, DESIRE_THRESHOLD_MAX)

    async def start(self) -> None:
        """后台常驻循环（借鉴 while True 潜意识）。"""
        logger.info("后台潜意识引擎启动，每 %d 秒轮询", TICK_SECONDS)
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.error("后台循环异常: %s", exc)
            await asyncio.sleep(TICK_SECONDS)

    def _is_resting(self, hour: int) -> bool:
        """是否休息期（跟随作息自适应，默认 22 点后 / 8 点前）。"""
        if self.rhythm:
            return self.rhythm.is_resting(hour)
        return hour >= 22 or hour < 8

    async def _tick(self) -> None:
        env = get_environment_context()
        hour = env["hour"]
        today = env["datetime"][:10]
        self._reset_daily_count(today)
        # 用户主动设的提醒在任何时间都要推送（含深夜）
        await self._check_reminders()
        # 目标到期提醒：3 天内到期或已逾期，推一次（不走每日上限）
        await self._check_goals()
        # 先发饭点/晚安关怀（晚安时间跟随自适应）
        await self._care_trigger(env, today)
        # 休息期：道晚安后静默，并做睡前总结 + 作息自适应
        if self._is_resting(hour):
            await self._before_sleep(today)
            return
        # 每日自我进化反思（借鉴 Hermes）
        await self._maybe_reflect(env, today)
        # 每日记忆整理（把零散记忆归纳成摘要，内部按天去重）
        await self._memory_reflection(env)
        # 空闲才自主探索，避免抢占前台（以 AgentLoop 的最后交互时间为准）
        last = self.loop.last_interaction if self.loop else self.last_interaction
        if time.time() - last >= IDLE_SECONDS:
            await self._autonomous_activity(env)

    async def _maybe_reflect(self, env: Dict, today: str) -> None:
        """每天 20:00-21:00 触发一次自我进化反思（Evolver.reflect）。"""
        if not self.evolver or self._last_reflect_day == today:
            return
        if not (20 <= env["hour"] < 21):
            return
        try:
            await self.evolver.reflect()
            logger.info("每日自我进化反思完成")
        except Exception as exc:
            logger.error("每日反思失败: %s", exc)
        finally:
            self._last_reflect_day = today

    async def _check_reminders(self) -> None:
        """检查到点提醒并推送（含深夜，用户主动设置的要准时）。"""
        try:
            from ..reminders import default_store
            store = default_store()
            now = datetime.now()
            for item in store.due(now):
                await self.on_push(f"⏰ 提醒：{item['content']}（{item['remind_at']}）")
                store.mark_done(item["id"])
        except Exception as exc:
            logger.error("提醒检查失败: %s", exc)

    async def _check_goals(self) -> None:
        """目标到期提醒：3 天内到期或已逾期，每天最多推一次。"""
        try:
            from ..goals import default_goal_store
            store = default_goal_store()
            today = datetime.now().strftime("%Y-%m-%d")
            key = f"goal_due_{today}"
            if self._reminded.get(key) == today:
                return
            due = store.due_soon(days=3)
            if not due:
                return
            lines = []
            for g in due:
                days_left = g.get("days_left", 0)
                if days_left < 0:
                    lines.append(f"「{g['title']}」已逾期 {abs(days_left)} 天，进度 {g.get('progress', 0)}%")
                elif days_left == 0:
                    lines.append(f"「{g['title']}」今天到期，进度 {g.get('progress', 0)}%")
                else:
                    lines.append(f"「{g['title']}」还有 {days_left} 天到期，进度 {g.get('progress', 0)}%")
            await self.on_push("🎯 目标提醒：\n" + "\n".join(lines))
            self._mark_reminded(key, today)
        except Exception as exc:
            logger.error("目标提醒检查失败: %s", exc)

    # ---- 主动关怀（概率化 + 每日上限：每天每种只掷一次骰子） ----
    async def _care_trigger(self, env: Dict, today: str) -> None:
        hour = env["hour"]
        # 早上问好（醒来时刻，晚睡晚起后跟随自适应醒来时间）
        wake_h = self.rhythm.wake_hour if self.rhythm else 6
        if wake_h <= hour < wake_h + 1 and self._reminded.get("morning") != today:
            await self._maybe_care("morning", "早上好呀，新的一天开始啦", today)
            return
        # 午饭关怀
        if 11 <= hour < 13 and self._reminded.get("lunch") != today:
            await self._maybe_care("lunch", "该吃午饭啦，记得好好吃饭哦 🍚", today)
            return
        # 晚安关怀（晚安时间跟随自适应）
        sleep_h = self.rhythm.sleep_hour if self.rhythm else 22
        if sleep_h <= hour < sleep_h + 1 and self._reminded.get("sleep") != today:
            await self._maybe_care("sleep", "夜深了，早点休息，注意身体 🌙", today)

    async def _maybe_care(self, key: str, intent: str, today: str) -> None:
        """对一种关怀掷一次骰子：掷出不发则记下“今天已决定不发”，本窗口不再重掷。

        关键：不发也要记 decided，否则每个 tick 重掷一次，掷到发为止 = 还是每天必发。
        """
        if self._reminded.get(f"{key}_decided") == today:
            return
        self._mark_reminded(f"{key}_decided", today)
        if not self._can_push_proactive(today):
            logger.info("关怀 %s：今日主动推送预算已用完，跳过", key)
            return
        if random.random() >= CARE_PROB.get(key, 0.4):
            logger.info("关怀 %s：概率掷骰未命中，今天跳过", key)
            return
        ok = await self._push_care(intent)
        if ok:
            self._mark_reminded(key, today)
            self._note_proactive_push(today)

    # ---- 睡前处理（休息期触发） ----
    async def _before_sleep(self, today: str) -> None:
        """进入休息期：作息自适应 + 用本地模型总结今天内容。"""
        if self.rhythm:
            self.rhythm.adjust()
        await self._daily_summary(today)

    async def _daily_summary(self, today: str) -> None:
        """睡前总结：用本地模型（Ollama，零成本）总结【用户本人说】的内容，存入记忆。"""
        if self._last_summary_day == today:
            return
        try:
            recent = self.memory.query("今天 用户 对话 互动 内容", n_results=12,
                                       category=USER_CATEGORIES)
            if not recent:
                return
            snippet = "\n".join(f"- {m['text']}" for m in recent)[:1000]
            model = self.router.get("ollama")
            if model is None or not getattr(model, "is_configured", True):
                logger.info("Ollama 未配置，跳过睡前总结")
                return
            await self.limiter.acquire("ollama")
            summary = await model.chat(
                [{"role": "user", "content":
                  f"回顾今天和用户的互动，用 2-3 句话总结今天发生了什么、用户的情绪状态、重要的事。"
                  "只总结用户本人说的内容，不要编造、不要加入任何 AI 的观点。\n"
                  f"用户今天说的内容：\n{snippet}"}],
                temperature=0.3)
            if summary.strip():
                self.memory.store(f"[每日总结] {summary.strip()}",
                                  category="daily_summary", importance=2)
                logger.info("睡前总结完成")
        except Exception as exc:
            logger.error("睡前总结失败: %s", exc)
        finally:
            self._last_summary_day = today
            self._mark_reminded("summary", today)  # 持久化，重启不重复总结

    async def _push_care(self, intent: str) -> bool:
        """用大脑生成一句温暖的关怀话，推送给用户。改版：只发 1 条，不发 2-3 条。"""
        try:
            env = get_environment_context()
            prompt = (
                f"你是用户的朋友。现在是{env.get('time_period','')}，"
                f"{env.get('weather','')}。请用自然、温暖、不啰嗦的口吻，"
                f"就「{intent}」发 1 条简短的主动关心话，像真人发微信一样，只发一句话。"
            )
            _, model = self.router.route("主动关怀", explicit_simple=False)
            await self.limiter.acquire("deepseek")
            text = await model.chat([{"role": "user", "content": prompt}],
                                    temperature=0.8)
            if not text.strip():
                return False
            await self.on_push(text.strip())
            return True
        except Exception as exc:
            logger.error("主动关怀失败: %s", exc)
            return False

    # ---- 自主探索（欲望值 + 冷却下限 + 每日上限 + 抛硬币，四道门） ----
    async def _autonomous_activity(self, env: Dict) -> None:
        """自主探索：四道门都过才考虑推。

        1. 今日预算：主动推送已满 2 条，今天不再推。
        2. 空闲：距上次交互不满 30 分钟不推（刚聊完不打扰）。
        3. 冷却下限：距上次主动推送不满 2 小时不推（下限，不是定时器）。
        4. 欲望值：空闲 tick 慢慢涨，涨满阈值后再抛一次硬币，抛输了欲望减半、
           阈值重随——所以间隔是随机的，有时几小时，有时几天都不推。
        未了解用户偏好前不乱推。
        """
        today = env["datetime"][:10]
        if not self._can_push_proactive(today):
            return
        last = self.loop.last_interaction if self.loop else self.last_interaction
        if time.time() - last < PUSH_IDLE_SECONDS:
            return
        if time.time() - self._last_push_ts < PUSH_FLOOR_SECONDS:
            return
        # 欲望值：只在空闲 tick 累积
        self._desire += random.uniform(DESIRE_GAIN_MIN, DESIRE_GAIN_MAX)
        if self._desire < self._desire_threshold:
            return
        # 抛硬币：涨满了也只有 50% 概率这次推
        if random.random() >= PUSH_COIN_FLIP:
            logger.info("探索：欲望已满但抛硬币未命中，这次不推，欲望减半")
            self._dampen_desire()
            return
        # 从记忆提取用户偏好（category=user_preference）
        prefs = self.memory.query("用户喜欢 兴趣 爱好 偏好", category="user_preference",
                                  n_results=5)
        if not prefs:
            # 还没了解用户，不乱推
            return
        await self._interest_research(env, prefs, today)

    async def _interest_research(self, env: Dict, prefs, today: str) -> None:
        """基于用户偏好探索：必须有真实搜索结果才推，没有搜索工具 / 搜不到就丢掉，不编造。"""
        try:
            pref_text = "；".join(p["text"] for p in prefs)[:300]
            # 没接入搜索工具：直接跳过，不让 LLM 编内容（原版就是在这里编“江夏有家咖啡店”）
            if self.plugins is None:
                logger.info("探索：未接入搜索工具，跳过编造式探索")
                self._dampen_desire()
                return
            # 第一步：只让 LLM 出一个搜索关键词，不让它直接编内容
            _, model = self.router.route("探索")
            await self.limiter.acquire("deepseek")
            query = await model.chat(
                [{"role": "user", "content":
                  f"用户偏好：{pref_text}\n"
                  "只输出一个搜索关键词（10 个字以内），用来搜一个用户可能感兴趣的真实新鲜事。"
                  "不要输出解释、不要输出内容本身，只要关键词。"}],
                temperature=0.7)
            query = query.strip().strip("「」\"'，。")
            if not query:
                self._dampen_desire()
                return
            # 第二步：真实搜索
            search_result = await self.plugins.call_tool("search_web", {"query": query})
            if (not search_result or "搜索失败" in search_result
                    or "没有搜到" in search_result or "http" not in search_result):
                logger.info("探索：搜索无有效结果，不推送")
                self._dampen_desire()
                return
            # 第三步：只基于搜索结果写分享，必须带上来源链接，不许加搜索结果之外的事实
            idea = await model.chat(
                [{"role": "user", "content":
                  f"用户偏好：{pref_text}\n\n搜索结果：\n{search_result[:1500]}\n\n"
                  "基于以上搜索结果，用 1-2 句口语像朋友一样分享，不要超过 2 句。"
                  "只说搜索结果里有的事，不许编细节、不许编店名地址价格；结尾附上搜索结果里的一个来源链接。"}],
                temperature=0.7)
            idea = idea.strip()
            if not idea:
                self._dampen_desire()
                return
            # 安全过滤：涉及危险/违法主题的探索不推送
            _danger = ("扫描", "破解", "攻击", "入侵", "WiFi密码", "wifi密码",
                       "端口", "漏洞", "渗透", "钓鱼", "偷取", "默认密码")
            if any(k in idea for k in _danger):
                logger.info("探索内容涉及危险主题，不推送")
                self._dampen_desire()
                return
            # 严格相关性判断：不相关或没价值就不推
            _, judge = self.router.route("相关性判断")
            await self.limiter.acquire("deepseek")
            verdict = await judge.chat(
                [{"role": "user", "content":
                  f"用户偏好：{pref_text}\n\n待分享内容：{idea}\n\n"
                  "这个内容是否与用户偏好高度相关、且值得主动分享？只回答「是」或「否」。"}],
                temperature=0.2)
            if "是" not in verdict:
                logger.info("探索内容与用户偏好不相关，不推送")
                self._dampen_desire()
                return
            self.memory.store(f"[Agent 后台探索] {idea}",
                              category="agent_thought", importance=1)
            await self.on_push(idea.strip())
            self._note_proactive_push(today)
            logger.info("已推送与用户偏好相关的探索内容")
        except Exception as exc:
            logger.error("兴趣探索失败: %s", exc)
            self._dampen_desire()

    async def _memory_reflection(self, env: Dict) -> None:
        """后台整理记忆（每天一次，避免重复归纳相同内容）。"""
        today = env.get("datetime", "")[:10]
        if self._last_memory_reflect_day == today:
            return
        try:
            if self.memory.count() == 0:
                return
            _, model = self.router.route("记忆反思", explicit_simple=True)
            await self.limiter.acquire("agnes")
            recent = self.memory.query("最近", n_results=5,
                                       category=USER_CATEGORIES)
            snippet = "；".join(m["text"] for m in recent)[:500]
            summary = await model.chat(
                [{"role": "user", "content":
                  f"把这些零散记忆归纳成 1-2 句精简摘要：{snippet}"}],
                temperature=0.3)
            if summary.strip():
                self.memory.store(f"[记忆归纳] {summary.strip()}",
                                  category="reflection", importance=1)
        except Exception as exc:
            logger.error("记忆反思失败: %s", exc)
        finally:
            self._last_memory_reflect_day = today
