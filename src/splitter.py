"""把 AI 回复拆成多条短消息（像真人一句一句发）。

LLM 生成层被要求用「换行分隔的短句」输出，这里在发送层兜底：
- 优先按换行拆分（LLM 自己分好的句子）
- 某一行过长时再按句末标点二次断句
- 过滤空行和 markdown 符号，得到干净的短消息列表
"""
from __future__ import annotations

import re

# 单条消息的最大长度（超出则按标点再断）
MAX_MSG_LEN = 40

# 句末标点（在此断句）
_SENT_END = re.compile(r"(?<=[。！？!?；;…])")

# 开头的 markdown 符号（去掉列表/标题噪声）
_LEADING_MD = re.compile(r"^[\s\-*#>\d.、]+")


def _clean(line: str) -> str:
    """去掉一行开头的 markdown 噪声，返回干净文本。"""
    s = _LEADING_MD.sub("", line.strip()).strip()
    return s


def _split_long(line: str, max_len: int) -> list[str]:
    """把过长的行按句末标点二次断句。"""
    if len(line) <= max_len:
        return [line]
    pieces = [p.strip() for p in _SENT_END.split(line) if p and p.strip()]
    if not pieces:
        return [line]
    result: list[str] = []
    buf = ""
    for p in pieces:
        if len(buf) + len(p) <= max_len:
            buf += p
        else:
            if buf:
                result.append(buf)
            buf = p
    if buf:
        result.append(buf)
    return result or [line]


def split_reply(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """把一段回复拆成短消息列表（保持顺序）。"""
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    msgs: list[str] = []
    for raw in text.split("\n"):
        line = _clean(raw)
        if not line:
            continue
        msgs.extend(_split_long(line, max_len))
    # 去重相邻完全相同的句子（LLM 偶发重复）
    deduped: list[str] = []
    for m in msgs:
        if not deduped or deduped[-1] != m:
            deduped.append(m)
    return deduped
