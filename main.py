"""Friend Agent 主入口。

TUI 界面（主）+ 后台潜意识引擎。微信/Telegram 等消息渠道是扩展能力，
通过 TUI 内 /message setup <name> 命令登录/配置。

用法：
    python main.py    # 启动 TUI + 后台引擎
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

sys_path = Path(__file__).parent
import sys
sys.path.insert(0, str(sys_path))

from src.models import DeepSeekClient, AgnesClient, OllamaClient
from src.router import ModelRouter
from src.usage import UsageTracker
from src.rhythm import RhythmTracker
from src.limiter import default_limiter
from src.memory import MemoryStore
from src.persona import Persona
from src.config_store import ConfigStore
from src.perception import start_weather_updater
from src.extensions import PluginManager
from src.extensions.builtin.note_tool import NoteTool, ClearMemoryTool
from src.extensions.builtin.game_tool import MakeGameTool
from src.engine.agent_loop import AgentLoop
from src.engine.background import BackgroundEngine
from src.engine.evolve import Evolver
from src.channels.tui import TUIChannel
from src.channels.weixin import WeixinChannel
from src.channels.manager import MessageChannelManager

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("friend-agent")


def build_core():
    """构建模型路由、限流、记忆、人设、插件。"""
    usage = UsageTracker()
    router = ModelRouter(DeepSeekClient(usage_tracker=usage),
                         AgnesClient(usage_tracker=usage),
                         OllamaClient(usage_tracker=usage))
    limiter = default_limiter()
    memory = MemoryStore("data/memory_db")
    persona = Persona("config/persona.json")
    plugins = PluginManager()
    # 内置插件（顶层 tool 实例自动注册）
    plugins.load_module("src.extensions.builtin.weather_tool")
    plugins.load_module("src.extensions.builtin.web_search")
    plugins.load_module("src.extensions.builtin.calc_tool")
    plugins.load_module("src.extensions.builtin.schedule_tool")
    plugins.load_module("src.extensions.builtin.run_command_tool")
    plugins.load_module("src.extensions.builtin.amap_tool")
    plugins.load_module("src.extensions.builtin.goal_tool")
    # 需要依赖注入的工具（显式注册）
    plugins.register_tool(NoteTool(memory))
    plugins.register_tool(ClearMemoryTool(memory))
    plugins.register_tool(MakeGameTool(router, limiter))
    return router, limiter, memory, persona, plugins, usage


async def amain() -> None:
    config_store = ConfigStore("config/.env")
    config_store.load()
    start_weather_updater()  # 后台线程更新天气，避免对话卡顿
    router, limiter, memory, persona, plugins, usage = build_core()
    rhythm = RhythmTracker()
    persona.bind_rhythm(rhythm)  # 让人设的迷糊语气跟随自适应作息
    loop = AgentLoop(router, limiter, memory, persona, plugins, rhythm=rhythm,
                     usage_tracker=usage)
    evolver = Evolver(router, limiter, memory, persona)

    # 预热本地嵌入模型（bge-m3 冷启动约 26s，后台加载避免首次对话卡顿）
    async def _warmup_memory() -> None:
        try:
            await asyncio.to_thread(memory.query, "预热", n_results=1)
            logger.info("记忆嵌入模型预热完成")
        except Exception as exc:
            logger.warning("嵌入模型预热失败: %s", exc)

    asyncio.create_task(_warmup_memory())

    # ---- 消息渠道管理（微信等扩展能力，TUI 内 /message 命令配置） ----
    manager = MessageChannelManager()
    manager.register("wechat", WeixinChannel("data"))
    # 未来扩展：manager.register("telegram", TelegramChannel("data"))

    # ---- 消息处理回调 ----
    async def handle(user_id: str, text: str) -> str:
        return await loop.respond(user_id, text)

    async def handle_stream(user_id: str, text: str):
        """流式回调：逐 chunk 产出（微信用，边生成边发送、逐句思考）。"""
        async for chunk in loop.respond_stream(user_id, text):
            yield chunk

    manager.set_handler(handle)
    manager.set_stream_handler(handle_stream)

    # ---- 自动启用已登录的渠道 -------
    for name in manager.names():
        ch = manager.get(name)
        if getattr(ch, "load_creds", None) and ch.load_creds():
            manager.enable(name)

    tool_names = [t["function"]["name"] for t in plugins.get_tool_schemas()]
    model_status = [(name, getattr(client, "is_configured", True))
                    for name, client in router.all().items()]
    boot_info = {
        "tools": tool_names,
        "skills": [],  # 技能包（预留，当前未接入）
        "models": model_status,
        "memory_count": memory.count(),
        "memory": memory,
        "channel_manager": manager,
        "usage_tracker": usage,
        "rhythm": rhythm,
        "router": router,   # 供 /memory summary 手动总结
        "limiter": limiter,
        "agent_loop": loop,  # 供 /memory summary 从对话历史总结
    }
    tui = TUIChannel(config_store, stream_handler=loop.respond_stream,
                     boot_info=boot_info)

    # ---- 主动推送广播（后台引擎 → TUI + 所有启用的渠道） ----
    async def broadcast(text: str) -> None:
        try:
            await tui.send("", text)
        except Exception as exc:
            logger.error("推送失败 tui: %s", exc)
        for ch in manager.active_channels():
            try:
                if getattr(ch, "name", "") == "weixin" and not getattr(ch, "_last_contact", ""):
                    await tui.send("", "⚠️ 微信还没建立联系，这条没发到微信（先用微信给 bot 发条消息）")
                    continue
                await ch.send("", text)
            except Exception as exc:
                logger.error("推送失败 %s: %s", ch.name, exc)

    bg = BackgroundEngine(router, limiter, memory, persona, on_push=broadcast,
                          loop=loop, evolver=evolver, rhythm=rhythm, plugins=plugins)

    # ---- 启动后台任务 + 已启用的渠道 ----
    tasks = [asyncio.create_task(bg.start())]
    for name in manager.names():
        if manager.is_enabled(name):
            await manager.start_channel(name)

    # ---- TUI 作为主界面（阻塞直至退出） ----
    try:
        await tui.start(handle)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
