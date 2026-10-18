"""TUI 终端界面（照搬 Pi Agent TUI 的视觉风格）。

对齐 Pi 的 dark theme 实际渲染：
- 页面背景 #18181e，用户消息 #343541 背景块，assistant 消息无背景
- 输入框：上下各一条灰色 ─ 横线（#505050），无左右边框、无圆角
- 底部 footer：dim 灰色状态栏（pwd）
- 无 logo header，启动即空白消息区
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .base import Channel, MessageHandler
from ..usage import THINKING_LEVELS
from ..memory import USER_CATEGORIES

logger = logging.getLogger("friend-agent")

StreamHandler = AsyncIterator[str]


def _patch_textual_transparent_bg() -> None:
    """修复 textual：alpha=0（透明）背景不输出背景色码，透出终端壁纸。

    textual 的 rich_style 把 Color(0,0,0,a=0) 渲染成纯黑 #000000（rich 库
    不支持 alpha），导致 CSS 的 transparent 无法透出终端背景。这里 patch 成
    bgcolor=None，让 rich 不输出背景码，壁纸即可透出。
    """
    from textual.style import RichStyle, Style, _get_simple_attributes

    def _make_rich_style(self):
        (background, foreground, bold, dim, italic, underline,
         underline2, reverse, strike, blink, link, _meta) = _get_simple_attributes(self)
        color = None if foreground is None else background + foreground
        # 关键：alpha==0 的透明背景 -> None（不输出背景码）
        bg = None if (background is None or background.a == 0) else background.rich_color
        return RichStyle(
            color=None if color is None else color.rich_color,
            bgcolor=bg,
            bold=bold, dim=dim, italic=italic, underline=underline,
            underline2=underline2, reverse=reverse, strike=strike,
            blink=blink, link=link, meta=None if _meta is None else self.meta,
        )

    Style.rich_style = property(_make_rich_style)
    Style.rich_style_with_offset = lambda self, x, y: _make_rich_style(self)


def _build_app(stream_handler, push_queue: asyncio.Queue, config_store, boot_info=None):
    """延迟导入 textual，构建 App。stream_handler: async (user_id, text) -> AsyncIterator[str]。"""
    _patch_textual_transparent_bg()
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Input, Markdown, Select, Static

    from ..config_store import PROVIDERS

    class LoginScreen(ModalScreen):
        """登录屏：方向键选 provider → 输入 API key → 保存到 .env。"""

        CSS = """
        LoginScreen { align: center middle; background: #18181eaa; }
        #login-box { width: 64; border: round #8abeb7; background: #1e1e24;
                     padding: 1 2; }
        #login-title { text-align: center; text-style: bold; color: #8abeb7; }
        #provider { margin: 1 0; }
        #apikey { margin: 1 0; }
        #hint { color: #b5bd68; }
        """

        def compose(self) -> ComposeResult:
            from textual.containers import Vertical
            with Vertical(id="login-box"):
                yield Static("🔑 选择模型提供商（方向键 ↑↓ 选择）", id="login-title")
                options = [(f"{p['name']} — {p['desc'][:22]}", p["id"]) for p in PROVIDERS]
                yield Select(options, prompt="选择一个提供商…", id="provider")
                yield Input(placeholder="输入 API key（回车保存，Esc 取消）",
                            password=True, id="apikey")
                yield Static("", id="hint")

        async def on_select_changed(self, event: Select.Changed) -> None:
            by_id = {p["id"]: p for p in PROVIDERS}
            p = by_id[event.value]
            inp = self.query_one("#apikey", Input)
            if p.get("needs_key"):
                inp.disabled = False
                inp.placeholder = f"输入 {p['key_var']}（回车保存）"
            else:
                inp.disabled = True
                inp.value = ""
                self.query_one("#hint", Static).update(
                    "Ollama 本地模型无需 key，回车即完成")

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            by_id = {p["id"]: p for p in PROVIDERS}
            p = by_id[self.query_one("#provider", Select).value]
            key = event.value.strip()
            hint = self.query_one("#hint", Static)
            if p.get("needs_key"):
                if not key:
                    hint.update("⚠️ API key 不能为空")
                    return
                config_store.set(p["key_var"], key)
                config_store.set(p["base_url_var"], p["default_base_url"])
                config_store.set(p["model_var"], p["default_model"])
                hint.update(f"✅ {p['name']} 已保存！重启 Friend Agent 后生效")
            else:
                hint.update(f"✅ {p['name']} 无需配置")
            await asyncio.sleep(1.2)
            self.dismiss()

    class HistoryInput(Input):
        """带历史翻页 + Tab 补全的输入框。"""

        BINDINGS = [
            ("up", "history_prev", "上一条"),
            ("down", "history_next", "下一条"),
            ("tab", "complete", "补全"),
            ("shift+tab", "cycle_thinking", "切换思考模式"),
        ]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._history: list[str] = []
            self._hist_index: int = -1
            self._draft: str = ""
            self.on_complete = None  # Tab 补全回调

        def action_complete(self) -> None:
            if self.on_complete:
                self.on_complete()

        def action_cycle_thinking(self) -> None:
            """Shift+Tab：循环切换思考模式（输入框聚焦时优先于 Screen 的 focus_previous）。"""
            app = self.app
            if hasattr(app, "action_cycle_thinking"):
                app.action_cycle_thinking()

        def add_to_history(self, text: str) -> None:
            text = text.strip()
            if text and (not self._history or self._history[-1] != text):
                self._history.append(text)
            self._hist_index = -1
            self._draft = ""

        def action_history_prev(self) -> None:
            if not self._history:
                return
            if self._hist_index == -1:
                self._draft = self.value
                self._hist_index = len(self._history) - 1
            elif self._hist_index > 0:
                self._hist_index -= 1
            self.value = self._history[self._hist_index]
            self.cursor_position = len(self.value)

        def action_history_next(self) -> None:
            if self._hist_index == -1:
                return
            self._hist_index += 1
            if self._hist_index >= len(self._history):
                self._hist_index = -1
                self.value = self._draft
            else:
                self.value = self._history[self._hist_index]
            self.cursor_position = len(self.value)

    class FriendAgentTUI(App):
        """Friend Agent 终端界面（Pi TUI 风格：极简，无 header）。"""

        BINDINGS = [("ctrl+c", "quit", "退出")]

        # 斜杠命令列表（名称 + 描述，用于自动补全）
        COMMANDS = [
            ("login", "配置模型（API key）"),
            ("help", "查看帮助"),
            ("status", "检测系统状态（Skill/MCP/记忆/作息等）"),
            ("thinking", "切换思考模式（off/low/medium/high/max）"),
            ("message", "消息渠道管理"),
            ("memory", "查看/清空记忆"),
            ("clear", "清空屏幕消息"),
            ("quit", "退出"),
        ]

        # 子命令（命令名 -> [(子命令, 描述), ...]）
        SUB_COMMANDS = {
            "memory": [("clear", "清空记忆"), ("map", "记忆图谱"),
                       ("summary", "总结"), ("graph", "关系图")],
            "message": [("setup", "登录/配置渠道"), ("disable", "停用渠道"),
                        ("status", "查看状态")],
            "thinking": [("high", "高"), ("medium", "中"), ("low", "低"),
                         ("max", "最高"), ("off", "关闭")],
        }

        # Pi dark theme 配色（背景透出终端底色，不强制背景色）
        CSS = """
        App { background: transparent; }
        Screen { background: transparent; height: 100%; }
        Screen:inline { height: 100%; border-top: none; border-bottom: none; }

        #messages { background: transparent; }

        .user-msg {
            background: #343541;
            color: #d4d4d4;
            width: 100%;
            padding: 1 1;
        }

        .assistant-msg {
            background: transparent;
            color: #d4d4d4;
            width: 100%;
            padding: 0 1;
            margin: 1 0 0 0;
        }

        .boot-msg {
            background: transparent;
            color: #d4d4d4;
            width: 100%;
            padding: 0 1;
        }

        #input {
            border: none;
            border-top: solid #505050;
            border-bottom: solid #505050;
            background: transparent;
            color: #d4d4d4;
            padding: 0 1;
        }
        #input:focus {
            border-top: solid #808080;
            border-bottom: solid #808080;
        }

        #suggestions {
            display: none;
            background: #1e1e24;
            border: round #505050;
            padding: 0 1;
            margin: 0 1;
        }

        #app-footer { height: 1; padding: 0 1; }
        """

        def __init__(self, stream_handler, push_queue: asyncio.Queue, config_store,
                     boot_info=None):
            super().__init__()
            self._stream_handler = stream_handler
            self._push_queue = push_queue
            self._config_store = config_store
            self._boot_info = boot_info or {}
            self._tui_user_id = "tui-user"
            self._busy = False
            self._suggestions: list = []          # 当前过滤出的命令建议
            self._suppress_suggestions = False    # 补全时抑制建议刷新

        def compose(self) -> ComposeResult:
            messages = VerticalScroll(id="messages")
            messages.can_focus = False  # 点击消息区不抢输入框焦点，输入始终进输入框
            yield messages
            yield Static("", id="suggestions")
            yield HistoryInput(id="input")
            yield Static("", id="app-footer")

        async def on_mount(self) -> None:
            self._show_boot_info()
            inp = self.query_one("#input", HistoryInput)
            inp.on_complete = self._complete_suggestion
            inp.focus()
            self.set_interval(1.0, self._drain_push_queue)
            self._refresh_status()
            self.set_interval(1.0, self._refresh_status)

        def _show_boot_info(self) -> None:
            """启动时显示加载的资源（对齐 Pi 的 loadedResources：[Section] + dim 列表）。"""
            info = self._boot_info
            lines: list[str] = []

            def section(title: str) -> None:
                lines.append(f"[bold #8abeb7]{title}[/]")

            def item(label: str, enabled: bool = True) -> None:
                if enabled:
                    lines.append(f"[#808080]  {label}: [/][#b5bd68]已启用[/]")
                else:
                    lines.append(f"[#808080]  {label}: 未启用[/]")

            # Message（消息渠道）
            section("Message")
            manager = info.get("channel_manager")
            if manager and manager.names():
                for name in manager.names():
                    item(name, manager.is_enabled(name))
            else:
                item("无消息渠道", enabled=False)
            lines.append("")

            # Tools（function calling 工具）
            section("Tools")
            tools = sorted(info.get("tools", []))
            if tools:
                for t in tools:
                    item(t)
            else:
                item("无工具", enabled=False)
            lines.append("")

            # Skill（技能包，与工具区分）
            section("Skill")
            skills = info.get("skills") or []
            if skills:
                for s in skills:
                    item(s)
            else:
                item("（未接入 Skill）", enabled=False)
            lines.append("")

            # MCP（预留，当前未接入）
            section("MCP")
            mcps = info.get("mcps") or []
            if mcps:
                for m in mcps:
                    item(m)
            else:
                item("（未接入 MCP）", enabled=False)
            lines.append("")

            # Memory
            section("Memory")
            lines.append(f"[#808080]  {info.get('memory_count', 0)} 条记忆[/]")

            boot = Static("\n".join(lines))
            boot.add_class("boot-msg")
            self.query_one("#messages", VerticalScroll).mount(boot)

        def _refresh_status(self) -> None:
            """刷新输入框下方状态栏（Pi footer 风格：用量/费用/上下文/模型/思考）。"""
            usage = self._boot_info.get("usage_tracker")
            footer = self.query_one("#app-footer", Static)
            if usage is None:
                footer.update("[#808080]friend-agent[/]")
                return
            footer.update(f"[#808080]{usage.status_line()}[/]")

        async def _drain_push_queue(self) -> None:
            try:
                while True:
                    text = self._push_queue.get_nowait()
                    self._append_message("ai", text)
            except asyncio.QueueEmpty:
                pass

        def _append_message(self, role: str, text: str) -> Markdown:
            msgs = self.query_one("#messages", VerticalScroll)
            md = Markdown(text)
            if role == "user":
                md.add_class("user-msg")
            else:
                md.add_class("assistant-msg")
            msgs.mount(md)
            msgs.scroll_end(animate=False)
            return md

        async def on_input_changed(self, event: Input.Changed) -> None:
            """输入变化时更新斜杠命令建议（对齐 Pi 的 autocomplete）。"""
            if self._suppress_suggestions:
                return
            text = event.value
            if text.startswith("/"):
                self._update_suggestions(text)
            else:
                self._hide_suggestions()

        def _update_suggestions(self, text: str) -> None:
            text = text.strip()
            if " " in text:
                # 有子命令：匹配子命令
                cmd_part, _, sub_part = text.partition(" ")
                cmd_name = cmd_part[1:] if cmd_part.startswith("/") else ""
                subs = self.SUB_COMMANDS.get(cmd_name, [])
                matches = [(f"/{cmd_name} {name}", desc) for name, desc in subs
                           if name.startswith(sub_part)]
            else:
                query = text[1:] if text.startswith("/") else text
                matches = [(f"/{name}", desc) for name, desc in self.COMMANDS
                           if name.lower().startswith(query)]
                # 精确输入某命令时，追加它的子命令建议（如 /memory → /memory map）
                subs = self.SUB_COMMANDS.get(query)
                if subs and len(matches) == 1 and matches[0][0] == f"/{query}":
                    matches.extend((f"/{query} {sn}", sd) for sn, sd in subs)
            self._suggestions = matches
            if not matches:
                self._hide_suggestions()
                return
            lines = []
            for i, (full_cmd, desc) in enumerate(matches):
                if i == 0:
                    lines.append(f"[bold #8abeb7]{full_cmd}[/]  [#666666]{desc}[/]")
                else:
                    lines.append(f"[#d4d4d4]{full_cmd}[/]  [#666666]{desc}[/]")
            sug = self.query_one("#suggestions", Static)
            sug.update("\n".join(lines))
            sug.display = True

        def _hide_suggestions(self) -> None:
            self._suggestions = []
            self.query_one("#suggestions", Static).display = False

        def _complete_suggestion(self) -> None:
            """Tab 补全第一条建议（命令或子命令）。"""
            if not self._suggestions:
                return
            full_cmd, _ = self._suggestions[0]
            self._suppress_suggestions = True
            try:
                inp = self.query_one("#input", HistoryInput)
                inp.value = full_cmd
                inp.cursor_position = len(inp.value)
            finally:
                self._suppress_suggestions = False
            self._hide_suggestions()

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            event.input.value = ""
            if text.startswith("/"):
                await self._handle_command(text)
                return
            if self._busy:
                return
            inp = self.query_one("#input", HistoryInput)
            inp.add_to_history(text)
            self._append_message("user", text)
            self._busy = True
            try:
                md = None
                full = ""
                async for chunk in self._stream_handler(self._tui_user_id, text):
                    full += chunk
                    if md is None:
                        md = self._append_message("ai", full)
                    else:
                        md.update(full)
                if full:
                    if md is None:
                        md = self._append_message("ai", full)
                    else:
                        md.update(full)
                elif md is None:
                    self._append_message("ai", "（没有回复）")
            except Exception as exc:
                logger.error("TUI 回复失败: %s", exc)
                if md is not None:
                    md.update(f"⚠️ 出错: {exc}")
                else:
                    self._append_message("ai", f"⚠️ 出错: {exc}")
            finally:
                self._busy = False
                self.query_one("#messages", VerticalScroll).scroll_end(animate=False)

        async def _handle_command(self, text: str) -> None:
            parts = text.strip().lower().split()
            head = parts[0] if parts else ""
            if head in ("/login", "/登录"):
                await self.push_screen(LoginScreen())
            elif head in ("/help", "/帮助", "/?"):
                self._append_message(
                    "ai",
                    "`/login` 配置模型 · `/status` 系统状态 · `/memory` 记忆 · "
                    "`/message` 渠道 · `/clear` 清屏 · `/quit` 退出")
            elif head in ("/status", "/状态"):
                self._handle_status()
            elif head == "/thinking":
                self._handle_thinking(parts)
            elif head == "/message":
                self._handle_message(parts)
            elif head == "/memory":
                self._handle_memory(parts)
            elif head in ("/clear", "/清屏"):
                self._clear_messages()
            elif head in ("/quit", "/exit", "/退出"):
                self.exit()
            else:
                self._append_message("ai", f"未知命令 `{text}`（/help 查看）")

        def _handle_memory(self, parts: list) -> None:
            """/memory 查看记忆，/memory clear 清空记忆。"""
            memory = self._boot_info.get("memory")
            if memory is None:
                self._append_message("ai", "记忆库不可用")
                return
            if len(parts) == 1:
                n = memory.count()
                if n == 0:
                    self._append_message("ai", "记忆库是空的")
                    return
                recent = memory.query("最近", n_results=min(n, 5))
                lines = [f"共有 {n} 条记忆，最近几条："]
                for m in recent:
                    lines.append(f"- {m['text']}")
                self._append_message("ai", "\n".join(lines))
            elif len(parts) == 2 and parts[1] == "clear":
                removed = memory.clear()
                self._append_message("ai", f"✅ 已清空记忆（删除 {removed} 条）")
            elif len(parts) == 2 and parts[1] == "map":
                self._show_memory_map(memory)
            elif len(parts) == 2 and parts[1] == "summary":
                asyncio.create_task(self._manual_summary())
            elif len(parts) == 2 and parts[1] == "graph":
                self._show_memory_graph(memory)
            else:
                self._append_message(
                    "ai", "用法：`/memory` 查看 · `/memory clear` 清空 · `/memory map` 图谱 · "
                    "`/memory summary` 总结 · `/memory graph` 关系图")

        def _show_memory_graph(self, memory) -> None:
            """生成记忆关系图谱 HTML（类似 Obsidian Graph View，节点像神经网络）。"""
            try:
                from ..graph import write_graph
                n, e = write_graph(memory)
                if n == 0:
                    self._append_message("ai", "记忆库是空的，没有可绘制的节点")
                    return
                self._append_message(
                    "ai",
                    f"🧠 已生成记忆关系图谱：{n} 个节点 · {e} 条关系边\n"
                    f"文件：`data/memory_graph.html`\n"
                    "用浏览器打开即可查看——节点按类别着色，像神经网络一样互相连接，"
                    "可拖拽、滚轮缩放、hover 看完整内容。")
            except Exception as exc:
                self._append_message("ai", f"生成关系图谱失败: {exc}")

        async def _manual_summary(self) -> None:
            """手动触发总结：从对话历史 + 记忆库总结【用户说的】内容，存入记忆。"""
            memory = self._boot_info.get("memory")
            router = self._boot_info.get("router")
            limiter = self._boot_info.get("limiter")
            agent_loop = self._boot_info.get("agent_loop")
            if not memory or not router:
                self._append_message("ai", "记忆总结不可用")
                return
            self._append_message("ai", "🔄 正在总结对话内容（本地 Ollama）…")
            try:
                # 收集用户说的内容：对话历史 + 记忆库
                texts: list = []
                if agent_loop:
                    for hist in getattr(agent_loop, "_history", {}).values():
                        for m in hist:
                            if m.get("role") == "user":
                                texts.append(m["content"])
                recent = memory.query("用户 对话 互动 内容", n_results=12,
                                     category=USER_CATEGORIES)
                for m in recent:
                    texts.append(m["text"])
                # 去重
                seen = set()
                unique = []
                for t in texts:
                    t = (t or "").strip()
                    if t and t not in seen:
                        seen.add(t)
                        unique.append(t)
                if not unique:
                    self._append_message("ai", "没有可总结的内容（先聊几句吧）")
                    return
                snippet = "\n".join(f"- {t}" for t in unique)[:2000]
                model = router.get("ollama")
                if model is None or not getattr(model, "is_configured", True):
                    self._append_message("ai", "⚠️ Ollama 未配置/未启动，无法总结")
                    return
                if limiter:
                    await limiter.acquire("ollama")
                summary = await model.chat(
                    [{"role": "user", "content":
                      f"回顾下面用户说过的话，用 2-3 句话总结用户的喜好、状态、重要事实。"
                      "只总结用户本人说的，不要编造、不要加入 AI 观点。\n用户说过：\n{snippet}"}],
                    temperature=0.3)
                if summary.strip():
                    memory.store(f"[手动总结] {summary.strip()}",
                                 category="daily_summary", importance=2)
                    self._append_message("ai", f"✅ 总结完成：\n{summary.strip()}")
                else:
                    self._append_message("ai", "总结失败：模型返回空")
            except Exception as exc:
                self._append_message("ai", f"总结失败: {exc}")

        def _show_memory_map(self, memory) -> None:
            """展示记忆图谱（按类别分组）。"""
            all_mems = memory.all()
            if not all_mems:
                self._append_message("ai", "记忆库是空的")
                return
            icons = {
                "user_preference": "🧡偏好",
                "plan": "📅计划",
                "fact": "📌事实",
                "note": "📝笔记",
                "reflection": "🔄反思",
                "agent_thought": "💭想法",
            }
            groups: dict = {}
            for m in all_mems:
                cat = (m.get("metadata") or {}).get("category", "note")
                groups.setdefault(cat, []).append(m["text"])
            lines = [f"记忆图谱 · 共 {len(all_mems)} 条："]
            for cat, texts in sorted(groups.items()):
                label = icons.get(cat, cat)
                lines.append(f"▪ {label}（{len(texts)} 条）")
                for t in texts[:8]:
                    lines.append(f"    · {t}")
            self._append_message("ai", "\n".join(lines))

        def _handle_status(self) -> None:
            """/status：检测系统状态（Skill / MCP / 消息渠道 / 记忆 / 模型 / 作息 / 用量）。"""
            info = self._boot_info
            lines = ["【系统状态】", ""]

            # Skill（工具）加载情况
            tools = sorted(info.get("tools", []))
            lines.append(f"Skill（{len(tools)} 个）：")
            if tools:
                lines.append("  " + " · ".join(tools) + "  ✅ 已启用")
            else:
                lines.append("  无")
            lines.append("")

            # MCP 加载情况
            mcps = info.get("mcps") or []
            lines.append(f"MCP（{len(mcps)} 个）：")
            if mcps:
                lines.append("  " + " · ".join(mcps) + "  ✅ 已启用")
            else:
                lines.append("  （未接入）")
            lines.append("")

            # 消息渠道
            manager = info.get("channel_manager")
            if manager and manager.names():
                lines.append("Message：")
                for name in manager.names():
                    state = "✅ 已启用" if manager.is_enabled(name) else "❌ 未启用"
                    lines.append(f"  {name}：{state}")
                lines.append("")

            # 记忆
            lines.append(f"Memory：{info.get('memory_count', 0)} 条记忆")
            lines.append("")

            # 模型
            models = info.get("models", [])
            if models:
                labels = [f"{name}（{'已配置' if ok else '未配置'}）" for name, ok in models]
                lines.append("Models：")
                lines.append("  " + " · ".join(labels))
                lines.append("")

            # 作息
            rhythm = info.get("rhythm")
            if rhythm:
                lines.append(
                    f"作息：晚安 {rhythm.sleep_hour} 点 · 醒来 {rhythm.wake_hour} 点（睡眠 8 小时）")
                lines.append("")

            # 用量
            usage = info.get("usage_tracker")
            if usage:
                lines.append(f"用量：{usage.status_line()}")

            self._append_message("ai", "\n".join(lines))

        def _handle_thinking(self, parts: list) -> None:
            """/thinking：查看/切换思考模式（off/low/medium/high/max）。"""
            usage = self._boot_info.get("usage_tracker")
            if usage is None:
                self._append_message("ai", "思考模式不可用")
                return
            if len(parts) == 1:
                self._append_message(
                    "ai",
                    f"当前思考模式：`{usage.thinking}`\n"
                    f"用法：`/thinking high|medium|low|max|off` · 或按 `Shift+Tab` 循环切换")
                return
            level = parts[1].lower()
            if level not in THINKING_LEVELS:
                self._append_message(
                    "ai", f"未知模式 `{level}`（可用：{'、'.join(THINKING_LEVELS)}）")
                return
            usage.set_thinking(level)
            self._append_message("ai", f"✅ 思考模式已设为 `{level}`")
            self._refresh_status()

        def action_cycle_thinking(self) -> None:
            """Shift+Tab：循环切换思考模式。"""
            usage = self._boot_info.get("usage_tracker")
            if usage is None:
                return
            level = usage.cycle_thinking()
            self._append_message("ai", f"💭 思考模式：`{level}`")
            self._refresh_status()

        def _handle_message(self, parts: list) -> None:
            """消息渠道管理：/message [status] · /message setup <name> · /message disable <name>。"""
            manager = self._boot_info.get("channel_manager")
            if manager is None:
                self._append_message("ai", "消息渠道管理不可用")
                return
            if len(parts) == 1 or (len(parts) == 2 and parts[1] == "status"):
                names = manager.names()
                if not names:
                    self._append_message("ai", "没有注册任何消息渠道")
                    return
                lines = ["消息渠道状态："]
                for name in names:
                    state = "✅ 已启用" if manager.is_enabled(name) else "❌ 未启用"
                    lines.append(f"- {name}：{state}")
                lines.append("配置：`/message setup <渠道名>` · 停用：`/message disable <渠道名>`")
                self._append_message("ai", "\n".join(lines))
            elif len(parts) == 3 and parts[1] == "setup":
                name = parts[2]
                if name == "wechat":
                    self._setup_wechat(manager)
                else:
                    self._append_message(
                        "ai", f"未知渠道 `{name}`（可用：{', '.join(manager.names())}）")
            elif len(parts) == 3 and parts[1] == "disable":
                name = parts[2]
                if name not in manager.names():
                    self._append_message("ai", f"未知渠道 `{name}`")
                    return
                asyncio.create_task(manager.stop_channel(name))
                self._append_message("ai", f"已停用渠道 `{name}`")
            else:
                self._append_message(
                    "ai", "用法：`/message` 状态 · `/message setup wechat` 登录 · `/message disable wechat` 停用")

        def _setup_wechat(self, manager) -> None:
            """扫码登录微信并启用渠道（二维码显示在消息区）。"""
            weixin = manager.get("wechat")
            if weixin is None:
                self._append_message("ai", "微信渠道未注册")
                return
            if weixin.is_logged_in:
                self._append_message("ai", "微信已绑定，无需重复登录")
                return
            self._append_message("ai", "正在生成微信登录二维码…")

            async def _do_login() -> None:
                def on_qr(qr_text: str) -> None:
                    self._append_message("ai", f"请用微信扫码登录：\n```\n{qr_text}\n```")
                ok = await weixin.qr_login(on_qr=on_qr)
                if ok:
                    await manager.start_channel("wechat")
                    self._append_message("ai", "✅ 微信绑定成功，消息渠道已启用！")
                else:
                    self._append_message("ai", "微信登录失败或已取消")

            asyncio.create_task(_do_login())

        def _clear_messages(self) -> None:
            """清空消息区。"""
            msgs = self.query_one("#messages", VerticalScroll)
            for child in list(msgs.children):
                child.remove()
            self._hide_suggestions()

    return FriendAgentTUI(stream_handler, push_queue, config_store, boot_info)


class TUIChannel(Channel):
    """TUI 渠道：在终端里与 Friend Agent 对话（流式）。"""

    name = "tui"

    def __init__(self, config_store=None, stream_handler=None, boot_info=None):
        self._push_queue: asyncio.Queue = asyncio.Queue()
        self._config_store = config_store
        self._stream_handler = stream_handler
        self._boot_info = boot_info
        self._app = None

    async def send(self, user_id: str, text: str) -> None:
        """主动推送：放入队列，由 TUI 定时 drain 显示。"""
        await self._push_queue.put(text)

    async def start(self, handler: MessageHandler) -> None:
        stream = self._stream_handler or (lambda uid, t: _non_stream(handler, uid, t))
        self._app = _build_app(stream, self._push_queue, self._config_store, self._boot_info)
        # inline 模式：不进入 alt screen，透出终端背景（对齐 Pi 的做法）
        await self._app.run_async(inline=True)


async def _non_stream(handler: MessageHandler, user_id: str, text: str):
    yield await handler(user_id, text)
