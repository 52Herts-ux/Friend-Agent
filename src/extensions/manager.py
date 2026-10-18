"""插件管理器：注册、发现、调度工具与钩子（借鉴 Pi 的扩展加载机制）。"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List

from .base import Hook, Tool

logger = logging.getLogger("friend-agent")


class PluginManager:
    """统一管理 Tool 与 Hook 插件。"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._hooks: List[Hook] = []

    # ---- 注册 ----
    def register_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        logger.info("注册工具: %s", tool.name)

    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)
        logger.info("注册钩子: %s", hook.name)

    def load_module(self, module_path: str) -> None:
        """从 Python 模块路径加载插件（模块内顶层 Tool/Hook 实例自动注册）。

        约定：模块内定义 `tool = XxxTool()` 或 `hook = XxxHook()`，
        或定义 `TOOLS = [...]` / `HOOKS = [...]` 列表。
        """
        mod = importlib.import_module(module_path)
        for obj in vars(mod).values():
            if isinstance(obj, Tool):
                self.register_tool(obj)
            elif isinstance(obj, Hook):
                self.register_hook(obj)
        for tools in getattr(mod, "TOOLS", []):
            self.register_tool(tools)
        for hooks in getattr(mod, "HOOKS", []):
            self.register_hook(hooks)

    # ---- 工具调度 ----
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values()]

    def has_tools(self) -> bool:
        return bool(self._tools)

    async def call_tool(self, name: str, args: Dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"未知工具: {name}"
        try:
            return await tool.run(**args)
        except Exception as exc:
            logger.error("工具 %s 执行失败: %s", name, exc)
            return f"工具执行出错: {exc}"

    # ---- 钩子调度 ----
    async def emit(self, event: str, **kwargs) -> None:
        for hook in self._hooks:
            fn = getattr(hook, event, None)
            if fn:
                try:
                    await fn(**kwargs)
                except Exception as exc:
                    logger.error("钩子 %s.%s 失败: %s", hook.name, event, exc)
