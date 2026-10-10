"""HTTP 抓取工具（``capability/tools/http_fetch.py``，``00`` §0.3 B / §4.4）。

一个 **server** 类工具：用 httpx 抓取公网页面 HTML，替代公网场景的浏览器。
（浏览器能力完全依赖客户端本地 Agent，服务端用此工具覆盖简单抓取。）

实现 ``ServerTool`` 协议（``capability/base.py``）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.capability.base import ToolContext, ToolResult

#: 允许的抓取目标协议（仅 http/https，禁止 file: 等）
_ALLOWED_SCHEMES = {"http", "https"}
#: 响应体最大字节
_MAX_BYTES = 512 * 1024
#: 默认超时（毫秒）
_DEFAULT_TIMEOUT_MS = 10000


class HttpFetchTool:
    """抓取一个公网 URL 的 HTML/文本内容。"""

    name: str = "http_fetch"
    description: str = (
        "抓取一个公网 HTTP(S) 页面并返回其文本内容（去除脚本与样式）。"
        "适用于简单的网页抓取场景；需要浏览器渲染或登录页时应使用本地浏览器工具。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的完整 URL（http/https）"},
            "max_chars": {"type": "integer", "description": "返回文本的最大字符数", "default": 20000},
        },
        "required": ["url"],
    }
    output_schema: dict[str, Any] | None = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "title": {"type": "string"},
            "text": {"type": "string"},
        },
    }
    category: str = "server"
    risk_level: str = "read"
    approval_required: bool = False
    timeout_ms: int = _DEFAULT_TIMEOUT_MS

    async def execute(self, args: dict[str, Any], tc: ToolContext) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, error="缺少 url 参数")
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme not in _ALLOWED_SCHEMES:
            return ToolResult(ok=False, error=f"仅支持 http/https 抓取，收到 scheme={scheme!r}")

        max_chars = int(args.get("max_chars") or 20000)

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"抓取失败: {exc}")

        if len(content) > _MAX_BYTES:
            content = content[:_MAX_BYTES]

        text = _extract_text(content, resp.headers.get("content-type", ""))
        return ToolResult(ok=True, value={"url": url, "text": text[:max_chars]})


def _extract_text(raw: bytes, content_type: str) -> str:
    """简单提取 HTML 文本（剥离脚本/样式标签）。"""
    import re

    # 尽量用正确的编码（优先 content-type 声明，回退 utf-8）
    try:
        charset = _charset_from_content_type(content_type)
        html = raw.decode(charset, errors="replace")
    except Exception:  # noqa: BLE001
        html = raw.decode("utf-8", errors="replace")

    # 剥除 script / style
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    # 去标签
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    # 折叠空白
    text = re.sub(r"[ \t]+", " ", html)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _charset_from_content_type(content_type: str) -> str:
    import re as _re

    m = _re.search(r"charset=([\w-]+)", content_type, _re.I)
    return m.group(1) if m else "utf-8"
