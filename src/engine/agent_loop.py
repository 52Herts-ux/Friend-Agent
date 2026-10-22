"""前台对话循环（借鉴 Pi `pi-agent-core` 的 agent-loop + 工具执行 + 钩子）。

处理一条用户消息：触发钩子 → 检索记忆 → 组装上下文 → 工具调用循环 → 生成回复
→ 更新历史 → 触发回复钩子 → 后台提取记忆。

工具调用循环是 Friend Agent「有手」的关键：Agent 可调用 PluginManager 里注册的
任何 Tool（天气、搜索、生成游戏…），后续加能力只需注册新 Tool，不改核心。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional

from ..memory import MemoryStore
from ..persona import Persona
from ..perception import get_environment_context
from ..router import ModelRouter
from ..limiter import RateLimitManager
from ..extensions import PluginManager
from ..security import wrap_tool_output
from .context import build_messages, trim_history
from pathlib import Path

logger = logging.getLogger("friend-agent")

MAX_TOOL_ROUNDS = 4  # 工具调用最多轮数，防止死循环

# 工具意图启发式：命中这些关键词才走工具循环，其余走真流式（保证首字响应快）
_TOOL_INTENT_HINTS = (
    # 天气
    "天气", "下雨", "气温", "温度", "多少度", "晴天", "阴天",
    # 计算
    "帮我算", "算一下", "计算", "等于多少", "开方", "平方",
    # 搜索
    "搜索", "搜一下", "查一下", "最新", "实时", "网上", "新闻",
    # 提醒
    "提醒", "记得", "记住", "记下", "别忘了",
    # 命令执行
    "执行命令", "命令", "运行", "跑一下", "curl", "wget", "下载",
    "安装", "git", "ls", "cat", "脚本", "代码", "编译", "bash", "shell",
    # 高德地图
    "路线", "导航", "怎么走", "怎么去", "地图", "在哪", "距离", "多远",
    # 文件/文章
    "文章", "文件", "读取", "查看文件", "看看文件", "读一下",
    # 笔记
    "记笔记", "记一下", "保存",
    # 游戏
    "游戏", "小游戏",
    # 记忆清理
    "清理", "清空", "忘记", "重置", "删除记忆", "删掉记忆",
)


def _tool_intent(text: str) -> bool:
    """判断用户消息是否明显需要工具调用。"""
    return any(hint in text for hint in _TOOL_INTENT_HINTS)


# 用户「对话结束语」：说了这些，Agent 保持安静不回复（像真人自然收尾）
_SILENT_END = {
    "好的", "好", "好滴", "好嘞", "行", "可以", "ok", "okay",
    "嗯", "嗯嗯", "哦", "哦哦", "知道了", "了解", "明白", "收到",
    "晚安", "拜拜", "再见", "睡了", "睡觉了", "困了", "先睡了",
}


def _is_silent_end(text: str) -> bool:
    """判断用户消息是否是「对话结束语」，是则 Agent 不回复。"""
    t = (text or "").strip().rstrip("。.!！~～  ")
    if not t or len(t) > 4:
        return False
    return t.lower() in _SILENT_END


# 记忆提取允许的类别（与 memory.USER_CATEGORIES 一致）
_MEMORY_CATEGORIES = {"user_preference", "plan", "fact", "note"}


def _parse_memory_lines(result: str) -> List[tuple]:
    """解析记忆提取模型的输出，只接受严格的「类别 | 内容」行。

    模型偶尔会跑偏：不按格式输出、甚至直接以对话口吻回答用户。
    这类行一律丢弃——绝不能像旧版那样把没有类别的行默认塞进
    user_preference，否则 Agent 自己的话会被存成「用户记忆」（见 PLAN 8.10）。
    """
    out = []
    for line in (result or "").strip().splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line or line in ("无", "无。", "None", "没有"):
            continue
        if "|" not in line:
            continue  # 不合格式（多半是模型跑偏成了对话），丢弃
        cat, content = line.split("|", 1)
        cat = cat.strip()
        content = content.strip()
        if cat not in _MEMORY_CATEGORIES or not content:
            continue
        out.append((cat, content))
    return out


# 上下文压缩：历史估算 token 超过窗口 80% 时压缩，压缩后保留最近 KEEP_RECENT 条
COMPACT_RATIO = 0.8
KEEP_RECENT = 6


def _estimate_tokens(text: str) -> float:
    """粗略估算 token 数（对齐 Muse 的按 token 压缩思路，不用字符数直接比窗口）。

    经验值：中文 1 字 ≈ 1.5 token，英文约 4 字符 ≈ 1 token。
    之前直接用 len(content) 比 128k 窗口，中文会低估、英文会高估。
    """
    if not text:
        return 0.0
    total = 0.0
    for ch in text:
        if ord(ch) > 0x2E7F:  # CJK 等宽字符
            total += 1.5
        else:
            total += 0.25
    return total


class AgentLoop:
    """有状态对话引擎：每个 user 维护独立对话历史。"""

    def __init__(self, router: ModelRouter, limiter: RateLimitManager,
                 memory: MemoryStore, persona: Persona,
                 plugins: Optional[PluginManager] = None, rhythm=None,
                 usage_tracker=None, history_path: str = "data/history.json"):
        self.router = router
        self.limiter = limiter
        self.memory = memory
        self.persona = persona
        self.plugins = plugins
        self.rhythm = rhythm  # 作息自适应追踪器
        self.usage_tracker = usage_tracker  # 用量统计（含思考模式）
        self._history_path = Path(history_path)
        self._history: Dict[str, List[Dict]] = self._load_history()
        self.last_interaction: float = time.time()

    def _load_history(self) -> Dict[str, List[Dict]]:
        """重启后恢复对话历史（之前只在内存里，重启就丢）。"""
        if not self._history_path.exists():
            return {}
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("对话历史加载失败: %s", exc)
        return {}

    def _save_history(self) -> None:
        """每轮对话后持久化历史。"""
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as exc:
            logger.warning("对话历史保存失败: %s", exc)

    def _history_for(self, user_id: str) -> List[Dict]:
        return self._history.setdefault(user_id, [])

    async def _should_reply(self, text: str) -> bool:
        """判断这条用户消息是否需要回复（模型判断，非死板词表）。

        - 先词表快速拦截常见结束语（省一次模型调用）
        - 再用本地 Ollama 判断（零成本），不可用则降级云端 Agnes
        - 都失败默认回复
        """
        if _is_silent_end(text):
            return False
        prompt = (
            "判断这条用户消息是否需要回复。"
            "如果是「好的」「知道了」「嗯」「晚安」「拜拜」这类表示结束、确认、告别、"
            "不需要再继续聊的话，回答「不回」；否则回答「回」。只回答两个字。\n"
            f"消息：{text}"
        )
        for name in ("ollama", "agnes"):
            try:
                model = self.router.get(name)
                if model is None or not getattr(model, "is_configured", True):
                    continue
                await self.limiter.acquire(name)
                verdict = await model.chat(
                    [{"role": "user", "content": prompt}], temperature=0.1)
                if "不回" in verdict or "不用回" in verdict:
                    return False
                if "回" in verdict:
                    return True
            except Exception as exc:
                logger.debug("回复判断失败(%s): %s", name, exc)
        return True  # 判断失败默认回复

    async def _maybe_compact(self, user_id: str) -> None:
        """当对话历史 token 接近窗口上限时，压缩旧历史 + 总结记忆。

        触发：历史估算 token 超过窗口 COMPACT_RATIO（80%）。
        压缩：旧对话（保留最近 KEEP_RECENT 条）总结成一段摘要，替换旧历史，
        并把摘要存入记忆。
        """
        history = self._history_for(user_id)
        if len(history) <= KEEP_RECENT + 4:
            return
        window = self.usage_tracker.context_window if self.usage_tracker else 128_000
        total = sum(_estimate_tokens(m.get("content", "")) for m in history)
        if total < window * COMPACT_RATIO:
            return
        old = history[:-KEEP_RECENT]
        recent = history[-KEEP_RECENT:]
        snippet = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in old)[:3000]
        try:
            _, model = self.router.route("压缩", explicit_simple=True)
            await self.limiter.acquire("agnes")
            summary = await model.chat(
                [{"role": "user", "content":
                  f"把下面的对话历史总结成一段简洁摘要，保留用户偏好、计划、重要事实，不要编造：\n{snippet}"}],
                temperature=0.3)
            summary = summary.strip()
        except Exception as exc:
            logger.error("上下文压缩失败: %s", exc)
            return
        if not summary:
            return
        self._history[user_id] = [{"role": "assistant",
                                   "content": f"（之前的对话摘要）{summary}"}] + recent
        self.memory.store(f"[对话压缩摘要] {summary}", category="reflection", importance=2)
        logger.info("上下文压缩: %d 条 -> %d 条", len(history), len(recent) + 1)

    async def respond(self, user_id: str, text: str) -> str:
        """处理一条用户消息，返回完整回复（含工具调用循环）。"""
        self.last_interaction = time.time()
        if self.plugins:
            await self.plugins.emit("on_user_message", user_id=user_id, text=text)

        env = get_environment_context()
        if self.rhythm:
            self.rhythm.note_activity(env["hour"])

        # 用户说了结束语（好的/晚安/嗯等），保持安静不回复
        if not await self._should_reply(text):
            return ""

        # 上下文快满时自动压缩旧历史
        await self._maybe_compact(user_id)

        messages = build_messages(
            self.persona, env, self.memory, text, self._history_for(user_id),
            thinking=(self.usage_tracker.thinking if self.usage_tracker else "off"))

        name, model = self.router.route(text)
        if not getattr(model, "is_configured", True):
            return f"⚠️ 模型 [{name}] 还没配置 API key，请在 TUI 里输入 /login 配置～"
        tools = self.plugins.get_tool_schemas() if (self.plugins and self.plugins.has_tools()) else None

        reply = await self._run_tool_loop(model, name, messages, tools)
        self._record(user_id, text, reply)
        await self._after_reply(user_id, text, reply)
        return reply

    def _record(self, user_id: str, text: str, reply: str) -> None:
        """把一轮对话写入短期历史并裁剪 + 持久化。"""
        hist = self._history_for(user_id)
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": reply})
        self._history[user_id] = trim_history(hist)
        self._save_history()

    async def _after_reply(self, user_id: str, text: str, reply: str) -> None:
        """回复后的生命周期钩子 + 后台异步提取记忆。"""
        if self.plugins:
            await self.plugins.emit("on_assistant_reply", user_id=user_id,
                                    text=text, reply=reply)
            await self.plugins.emit("on_turn_end", user_id=user_id)
        asyncio.create_task(self._extract_memories(text, reply))

    async def respond_stream(self, user_id: str, text: str):
        """流式回复（TUI/微信用）。

        始终把 tools 传给模型，让 LLM 自己决定是否调用工具（对齐 Pi/Hermes，
        不用关键词预判）；无工具时流式输出文本，保证普通聊天首字响应快。
        """
        self.last_interaction = time.time()
        if self.plugins:
            await self.plugins.emit("on_user_message", user_id=user_id, text=text)

        env = get_environment_context()
        if self.rhythm:
            self.rhythm.note_activity(env["hour"])

        # 用户说了结束语（好的/晚安/嗯等），保持安静不回复
        if not await self._should_reply(text):
            return

        # 上下文快满时自动压缩旧历史
        await self._maybe_compact(user_id)

        messages = build_messages(
            self.persona, env, self.memory, text, self._history_for(user_id),
            thinking=(self.usage_tracker.thinking if self.usage_tracker else "off"))

        name, model = self.router.route(text)
        if not getattr(model, "is_configured", True):
            msg = f"⚠️ 模型 [{name}] 还没配置 API key，请在 TUI 里输入 /login 配置～"
            yield msg
            return

        tools = self.plugins.get_tool_schemas() if (self.plugins and self.plugins.has_tools()) else None

        # 第一轮：流式 + tools，LLM 自己决定是否调工具
        await self.limiter.acquire(name)
        content_parts: List[str] = []
        tool_calls = None
        try:
            if tools:
                async for kind, data in model.chat_stream_with_tools(messages, tools=tools):
                    if kind == "text":
                        content_parts.append(data)
                        yield data
                    elif kind == "tool_calls":
                        tool_calls = data
            else:
                async for delta in model.chat_stream(messages):
                    content_parts.append(delta)
                    yield delta
        except Exception as exc:
            logger.error("模型 %s 流式失败: %s", name, exc)
            err = "（抱歉，我刚刚走神了，能再说一遍吗？😅）"
            yield err
            content_parts.append(err)

        reply = "".join(content_parts).strip()

        # 有工具调用：执行工具，后续用非流式工具循环完成
        if tool_calls and tools:
            messages.append({
                "role": "assistant", "content": reply or "",
                "tool_calls": [
                    {"id": tc.get("id", ""), "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                args = {}
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self.plugins.call_tool(tc["name"], args)
                result = wrap_tool_output(tc["name"], result)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": result})
            reply = await self._run_tool_loop(model, name, messages, tools)
            for ch in reply:
                yield ch

        if reply:
            self._record(user_id, text, reply)
            await self._after_reply(user_id, text, reply)

    async def _run_tool_loop(self, model, name: str,
                             messages: List[Dict], tools) -> str:
        """工具调用循环：LLM → 工具 → LLM → ...，直到无工具调用。"""
        for _ in range(MAX_TOOL_ROUNDS):
            await self.limiter.acquire(name)
            try:
                content, tool_calls = await model.chat_with_tools(messages, tools=tools)
            except Exception as exc:
                logger.error("模型 %s 调用失败: %s", name, exc)
                return "（抱歉，我刚刚走神了，能再说一遍吗？😅）"

            if not tool_calls:
                return content or ""

            # 追加 assistant 工具调用消息
            messages.append({
                "role": "assistant", "content": content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })
            # 执行每个工具并追加结果（工具输出按不可信数据包装，防注入）
            for tc in tool_calls:
                args = {}
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self.plugins.call_tool(tc.function.name, args)
                result = wrap_tool_output(tc.function.name, result)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": result})
        return "（工具调用有点多，我先歇一下～）"

    async def _extract_memories(self, user_text: str, reply: str) -> None:
        """用省钱层（Agnes）提取【用户本人说】的信息，按类别写入向量库。

        修复：之前不管什么都存成 user_preference，人和目标都混在一起。
        现在按类别存：user_preference（喜好）/ plan（计划/目标）/
        fact（事实）/ note（其他值得记的）。
        """
        try:
            prompt = (
                "从「用户说」中提取【用户本人】值得长期记住的信息，"
                "每条一行，格式：类别 | 内容。\n"
                "类别只能是这 4 个：user_preference（喜好）、plan（计划/目标/考试/找工作）、"
                "fact（事实，如学校、城市、设备）、note（其他值得记的）。\n"
                "只提取用户自己说的内容，不要提取任何第三方或 AI 的观点。"
                "临时的查询/问路/查路线/问天气不是计划也不是事实，不要提取。"
                "如果没有值得长期记住的，输出「无」。\n"
                f"用户说：{user_text}"
            )
            _, model = self.router.route("提取记忆", explicit_simple=True)
            await self.limiter.acquire("agnes")
            result = await model.chat(
                [{"role": "user", "content": prompt}], temperature=0.2)
            for cat, content in _parse_memory_lines(result):
                self.memory.store(content, category=cat, importance=2)
        except Exception as exc:
            logger.debug("记忆提取失败（不影响主流程）: %s", exc)
