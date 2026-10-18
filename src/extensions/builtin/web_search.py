"""联网搜索工具：DuckDuckGo 免费搜索（无需 key），结果喂回 LLM 综合。

用 requests + DDG html 接口，解析标题/摘要/真实链接。若本机有 HTTP(S)_PROXY，
requests 会自动走代理（国内网络下可借此访问）。
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..base import Tool

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _clean_url(href: str) -> str:
    """把 DDG 的跳转链接还原成真实 URL。"""
    href = unescape(href)
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        target = qs.get("uddg", [href])[0]
        return unquote(target)
    if href.startswith("//"):
        return "https:" + href
    return href


def _strip_tags(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", s)).strip()


class WebSearchTool(Tool):
    name = "search_web"
    description = (
        "联网搜索实时信息（网页/新闻/事实）。当用户询问你不知道的、"
        "需要最新信息、或需要查证的内容时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词（简洁，如「今天天气 北京」）"}
        },
        "required": ["query"],
    }

    async def run(self, query: str = "") -> str:
        if not query.strip():
            return "搜索关键词不能为空"
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": UA},
                timeout=12,
            )
            resp.raise_for_status()
            results = self._parse(resp.text)
            if not results:
                return f"没有搜到「{query}」的相关结果，换个关键词试试？"
            lines = [f"「{query}」搜索结果（前 {len(results)} 条）："]
            for i, (title, snippet, url) in enumerate(results, 1):
                lines.append(f"{i}. {title}")
                if snippet:
                    lines.append(f"   {snippet}")
                lines.append(f"   {url}")
            return "\n".join(lines)
        except Exception as exc:
            return f"搜索失败: {exc}（可能是网络或代理问题）"

    @staticmethod
    def _parse(html: str):
        """解析 DDG html 结果：标题 / 摘要 / 链接。"""
        out = []
        pattern = (
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>'
        )
        for m in re.finditer(pattern, html, re.S):
            url = _clean_url(m.group(1))
            title = _strip_tags(m.group(2))
            snippet = _strip_tags(m.group(3))
            # 过滤广告与无效项
            if not title or title.startswith("<"):
                continue
            if any(k in url for k in ("ad_domain", "ad_provider", "y.js?", "ad_type")):
                continue
            out.append((title, snippet, url))
        return out


tool = WebSearchTool()  # 供 PluginManager.load_module 自动注册
