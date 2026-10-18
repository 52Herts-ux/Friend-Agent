"""插件系统：工具（Tool）与钩子（Hook）的注册与调度。"""
from .base import Tool, Hook
from .manager import PluginManager

__all__ = ["Tool", "Hook", "PluginManager"]
