#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LarkSheetsDataSource — Lark (Feishu) online spreadsheets via the Lark MCP server.

Unlike the SQL/Excel sources, this one does not talk to Lark directly: it goes
through the already-running Lark MCP server (registered in MCP settings), reusing
its auth (tenant_access_token) and tool surface. We:

  1. resolve a "read spreadsheet values" tool on that MCP server,
  2. call it for each sheet / range,
  3. parse Lark's value-matrix response into DataFrames,
  4. load them into a DuckDB in-memory connection — after which schema / query /
     preview behave exactly like every other source.

Lark MCP tool names differ across server versions, so the read tool can be set
explicitly; otherwise we auto-discover a likely candidate from the server's
advertised tools.
"""
import json
import logging
from typing import List, Optional, Tuple

import pandas as pd

from ._utils import (
    _clean_identifier, _dedup_columns, _detect_header_row,
    _list_tables, _new_conn, _preview_table_dict, _query, _register,
    _table_schema_str,
)
from .base import DataSource

log = logging.getLogger(__name__)

# Substrings used to auto-discover a "read values" tool when none is configured.
_READ_TOOL_HINTS = ("values_batch_get", "values.batchGet", "values_get",
                     "values.get", "reading_a_single_range", "values", "read")
_LIST_SHEET_HINTS = ("query", "metainfo", "sheets", "get_spreadsheet")


def _extract_values(payload) -> Optional[List[list]]:
    """Pull the 2-D value matrix out of a variety of Lark response shapes."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None

    # Common shapes:
    #   {"data": {"valueRange": {"values": [...]}}}
    #   {"data": {"valueRanges": [{"values": [...]}]}}
    #   {"valueRange": {"values": [...]}}
    data = payload.get("data", payload)
    if isinstance(data, dict):
        vr = data.get("valueRange")
        if isinstance(vr, dict) and isinstance(vr.get("values"), list):
            return vr["values"]
        vrs = data.get("valueRanges")
        if isinstance(vrs, list) and vrs and isinstance(vrs[0], dict):
            return vrs[0].get("values")
        if isinstance(data.get("values"), list):
            return data["values"]
    return None


class LarkSheetsDataSource(DataSource):
    """Load Lark spreadsheet ranges through the Lark MCP server."""

    def __init__(
        self,
        server_id: str,
        spreadsheet_token: str,
        ranges: Optional[List[str]] = None,
        read_tool: str = "",
        display_name: str = "",
    ):
        from agent.mcp_manager import get_mcp_manager

        self._mcp = get_mcp_manager()
        self._server_id = server_id
        self._token = spreadsheet_token
        self.name = display_name or f"Lark/{spreadsheet_token[:8]}"

        self._read_tool = read_tool or self._discover_tool(_READ_TOOL_HINTS)
        if not self._read_tool:
            raise ValueError(
                "未能在 Lark MCP 服务器上找到读取表格的工具，请在配置中显式指定 read_tool。"
            )

        self._conn = _new_conn()
        self._tables: List[str] = []
        self._load(ranges or [])

    # ── MCP tool resolution / invocation ─────────────────────────────────────

    def _discover_tool(self, hints) -> str:
        """Find a tool on the configured server whose name matches a hint."""
        tools = self._mcp.get_server_tools(self._server_id)
        names = [t.get("name", "") for t in tools]
        for hint in hints:
            for name in names:
                if hint.lower() in name.lower():
                    return name
        return ""

    def _call(self, tool: str, args: dict) -> str:
        full = f"mcp__{self._server_id}__{tool}"
        return self._mcp.call_tool(full, args)

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self, ranges: List[str]):
        # If caller did not pin ranges, read the whole spreadsheet (Lark accepts
        # the bare token / first sheet for most "read values" tools).
        targets = ranges or [""]
        for idx, rng in enumerate(targets):
            args = {"spreadsheet_token": self._token}
            if rng:
                args["range"] = rng
            raw = self._call(self._read_tool, args)
            values = _extract_values(raw)
            if not values or len(values) < 2:
                log.warning("[Lark] range %r returned no usable values", rng or "(all)")
                continue
            df = self._matrix_to_df(values)
            if df is None:
                continue
            table = _clean_identifier(rng) or f"sheet{idx + 1}"
            _register(self._conn, table, df)
            self._tables.append(table)

        if not self._tables:
            raise ValueError("Lark 表格中未发现有效数据，请检查文档 token / range / 权限。")

    @staticmethod
    def _matrix_to_df(values: List[list]) -> Optional[pd.DataFrame]:
        header_idx = _detect_header_row(values)
        header = [str(c) if c is not None else "" for c in values[header_idx]]
        data = values[header_idx + 1:]
        if not data:
            return None
        df = pd.DataFrame(data)
        # Align width to header.
        df = df.reindex(columns=range(len(header)))
        df.columns = _dedup_columns([_clean_identifier(c) for c in header])
        df.replace("", pd.NA, inplace=True)
        df = df.dropna(how="all")
        return df if not df.empty else None

    # ── DataSource contract (DuckDB-backed, same as gsheets) ─────────────────

    def get_schema(self) -> str:
        parts: List[str] = []
        for table in self._tables:
            rows = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            parts.append(_table_schema_str(self._conn, table, rows))
        return "\n\n".join(parts)

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        return _query(self._conn, sql)

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
        if _df is not None:
            _register(self._conn, table_name, _df)
            rows = len(_df)
        else:
            try:
                self._conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS ({sql})')
                rows = self._conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
            except Exception as exc:
                return f"Error building analysis table: {exc}"
        if table_name not in self._tables:
            self._tables.append(table_name)
        return _table_schema_str(self._conn, table_name, rows)

    def list_tables(self) -> List[str]:
        return _list_tables(self._conn)

    def get_preview(self) -> List[dict]:
        result = []
        for t in self._tables:
            try:
                cols = [r[0] for r in self._conn.execute(f'DESCRIBE "{t}"').fetchall()]
                total = self._conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                result.append({"name": t, "columns": cols, "total_rows": total})
            except Exception:
                continue
        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return _preview_table_dict(self._conn, table_name, table_name, max_rows)
