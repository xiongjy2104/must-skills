# -*- coding: utf-8 -*-
"""Export an analysis report into a Lark (Feishu) online document via Lark MCP.

The agent already speaks to Lark through the Lark MCP server (the same one used
by the Lark spreadsheet data source). This module reuses that channel to:

  1. create a new Lark Doc,
  2. append the report's sections as heading + text blocks.

Lark MCP tool names and block schemas differ across server versions, so tools
are auto-discovered (with optional explicit overrides) and every call degrades
gracefully: if block insertion is unsupported, the freshly created (empty) doc
link is still returned so the user gets something usable.

Note on charts: embedding images into a Lark Doc requires Lark's media-upload
API, which most MCP servers do not expose as a simple tool. Chart titles are
therefore written as text references; native image embedding is left as a
follow-up that depends on the specific Lark MCP server's capabilities.
"""
import json
import logging
import re
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

_CREATE_DOC_HINTS = ("docx.v1.document.create", "document_create",
                     "create_document", "docx_create", "create_doc")
_CREATE_BLOCK_HINTS = ("documentBlockChildren.create", "block_children",
                       "create_block", "blocks_create", "append_block")


def _find_tool(mcp_manager, server_id: str, hints) -> str:
    """Find a tool on the server whose name matches any hint substring."""
    names = [t.get("name", "") for t in mcp_manager.get_server_tools(server_id)]
    for hint in hints:
        for name in names:
            if hint.lower() in name.lower():
                return name
    return ""


def _resolve_server_id(mcp_manager, server_id: str) -> str:
    """Use the given server_id, else auto-pick a connected server whose id
    looks like Lark/Feishu."""
    if server_id:
        return server_id
    for st in mcp_manager.get_all_status():
        sid = st.get("server_id", "")
        if st.get("status") == "connected" and re.search(r"lark|feishu|飞书", sid, re.I):
            return sid
    return ""


def _extract_doc_ref(raw: str) -> Tuple[str, str]:
    """Pull (document_id, url) out of a create-document MCP response."""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return "", ""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    doc = data.get("document", data) if isinstance(data, dict) else {}
    doc_id = doc.get("document_id") or doc.get("objToken") or data.get("document_id", "")
    url = doc.get("url") or data.get("url", "")
    return doc_id, url


def _heading_block(level: int, text: str) -> dict:
    """A Lark docx heading block (heading1..heading9 → block_type 3..11)."""
    key = f"heading{min(max(level, 1), 9)}"
    return {"block_type": 2 + level, key: {"elements": [
        {"text_run": {"content": text}}]}}


def _text_block(text: str) -> dict:
    """A Lark docx plain text block (block_type 2)."""
    return {"block_type": 2, "text": {"elements": [
        {"text_run": {"content": text}}]}}


def export_to_lark_doc(
    mcp_manager,
    title: str,
    sections: list,
    server_id: str = "",
    chart_titles: Optional[List[str]] = None,
    folder_token: str = "",
    create_tool: str = "",
    block_tool: str = "",
) -> Tuple[bool, str]:
    """Create a Lark Doc and fill it with the report sections.

    Returns (ok, message). `message` is user-facing markdown (a doc link on
    success, an error explanation otherwise).
    """
    server_id = _resolve_server_id(mcp_manager, server_id)
    if not server_id:
        return False, ("未找到已连接的 Lark MCP 服务器。请先在「MCP 设置」中添加并连接 "
                       "Lark MCP，或在命令中指定 server_id。")

    create_tool = create_tool or _find_tool(mcp_manager, server_id, _CREATE_DOC_HINTS)
    if not create_tool:
        return False, f"Lark MCP 服务器「{server_id}」未提供创建文档的工具。"

    # 1. Create the document.
    create_args = {"title": title}
    if folder_token:
        create_args["folder_token"] = folder_token
    raw = mcp_manager.call_tool(f"mcp__{server_id}__{create_tool}", create_args)
    if isinstance(raw, str) and raw.startswith("[MCP ERROR]"):
        return False, f"创建 Lark 文档失败：{raw}"

    doc_id, url = _extract_doc_ref(raw)
    if not doc_id:
        return False, f"已调用创建文档工具，但无法解析返回的文档 ID。原始返回：{str(raw)[:300]}"

    link = f"[📄 打开 Lark 文档]({url})" if url else f"文档 ID：{doc_id}"

    # 2. Build blocks from sections (+ a chart reference note).
    blocks: List[dict] = [_heading_block(1, title)]
    for i, sec in enumerate(sections, 1):
        if isinstance(sec, dict):
            heading = sec.get("heading", f"Section {i}")
            content = sec.get("content", "")
        else:
            heading, content = str(sec), ""
        blocks.append(_heading_block(2, heading))
        if content:
            blocks.append(_text_block(content))
    if chart_titles:
        blocks.append(_heading_block(2, "图表"))
        blocks.append(_text_block("本次分析包含图表：" + "、".join(chart_titles)))

    block_tool = block_tool or _find_tool(mcp_manager, server_id, _CREATE_BLOCK_HINTS)
    if not block_tool:
        return True, (f"✅ Lark 文档「{title}」已创建（共 {len(sections)} 节）。\n\n{link}\n\n"
                      f"⚠️ 该 Lark MCP 未提供写入内容块的工具，文档暂为空白，请手动粘贴或"
                      f"配置支持 block 写入的 MCP。")

    # 3. Append blocks. document_id doubles as the root block id in Lark docx.
    append_args = {
        "document_id": doc_id,
        "block_id": doc_id,
        "children": blocks,
        "index": 0,
    }
    raw2 = mcp_manager.call_tool(f"mcp__{server_id}__{block_tool}", append_args)
    if isinstance(raw2, str) and raw2.startswith("[MCP ERROR]"):
        return True, (f"✅ Lark 文档「{title}」已创建，但写入内容失败：{raw2}\n\n{link}")

    note = f"（含 {len(chart_titles)} 张图表引用）" if chart_titles else ""
    return True, (f"✅ Lark 文档「{title}」已生成，共 {len(sections)} 节{note}。\n\n{link}")
