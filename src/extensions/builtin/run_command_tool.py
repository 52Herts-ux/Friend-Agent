"""Shell 命令执行工具：让 Agent 执行 Linux 命令（带超时、输出截断、危险拦截）。

安全设计：
- 超时 30 秒自动终止，避免命令卡死
- 输出截断到 4000 字符，避免超长输出撑爆上下文
- 危险命令黑名单拦截（删根、格式化磁盘、写盘、关机、fork 炸弹等）
"""
from __future__ import annotations

import asyncio

from ..base import Tool

TIMEOUT_SECONDS = 30
MAX_OUTPUT = 4000

# 危险命令黑名单（子串匹配，命中即拒绝执行）
_DANGEROUS = (
    "rm -rf /", "rm -fr /", "rm -rf /*", "rm -rf ~", "rm -rf $HOME",
    "mkfs", "dd if=", "dd of=/dev/", "of=/dev/sd", "of=/dev/nvme",
    ":(){ :|:& };:", "fork bomb",
    "shutdown", "reboot", "poweroff", "halt",
    "> /dev/sd", "> /dev/hd", "> /dev/nvme",
    "chmod -R 777 /",
)


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "在本地 Linux 环境执行一条 shell 命令，返回标准输出、错误和退出码。"
        "当用户需要实际执行命令才能完成的任务时使用，例如：查看文件、"
        "列目录、安装/运行程序、git 操作、跑脚本等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令（单条，如 'ls -la'、'git status'）"
            }
        },
        "required": ["command"],
    }

    async def run(self, command: str = "") -> str:
        cmd = (command or "").strip()
        if not cmd:
            return "命令为空"

        # 危险命令拦截
        for d in _DANGEROUS:
            if d in cmd:
                return f"⛔ 已拦截：命令包含危险操作「{d}」，拒绝执行。"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return f"⏱️ 命令执行超时（>{TIMEOUT_SECONDS}s），已强制终止：{cmd}"
        except Exception as exc:
            return f"命令执行失败: {exc}"

        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        code = proc.returncode

        parts: list[str] = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        result = "\n".join(parts).strip()
        if not result:
            result = f"(无输出，退出码 {code})"
        elif code != 0:
            result += f"\n(退出码 {code})"

        if len(result) > MAX_OUTPUT:
            result = result[:MAX_OUTPUT] + f"\n…(输出已截断，共 {len(result)} 字符)"
        return result


tool = RunCommandTool()  # 供 PluginManager.load_module 自动注册
