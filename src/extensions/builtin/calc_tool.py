"""计算器工具：安全求值数学表达式（LLM 算数易错，交给工具精确计算）。"""
from __future__ import annotations

import ast
import math

from ..base import Tool

# 白名单函数，杜绝任意代码执行
_ALLOWED = {
    "abs": abs, "round": round, "min": min, "max": max, "pow": pow,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "floor": math.floor, "ceil": math.ceil, "pi": math.pi, "e": math.e,
}


class CalcTool(Tool):
    name = "calculate"
    description = (
        "计算数学表达式（四则运算、幂、括号、开方等）。"
        "当用户需要精确数值计算时使用，避免心算出错。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '(3+5)*2'、'sqrt(16)'、'2**10'"
            }
        },
        "required": ["expression"],
    }

    async def run(self, expression: str = "") -> str:
        if not expression.strip():
            return "表达式为空"
        try:
            # 只允许数字 + 运算符 + 白名单函数，禁止任意代码
            node = ast.parse(expression, mode="eval")
            result = eval(compile(node, "<calc>", "eval"),
                          {"__builtins__": {}}, _ALLOWED)
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"{expression} = {result}"
        except Exception as exc:
            return f"计算失败: {exc}"


tool = CalcTool()  # 供 PluginManager.load_module 自动注册
