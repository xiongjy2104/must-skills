#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MaxComputeDataSource — Alibaba DataWorks backend (MaxCompute / ODPS).

DataWorks runs its compute on MaxCompute (ODPS). We talk to it through the
`pyodps` SDK: schema/metadata come from the ODPS catalog, queries run as
MaxCompute SQL, and results are pulled into a DuckDB in-memory connection so
that the analysis/charting layer (which speaks DuckDB SQL) behaves exactly the
same as it does for Excel / CSV sources.

Mirrors the SQLDataSource contract: live source tables are queried on ODPS,
while runtime `analysis_data` tables are materialised in the DuckDB cache.
"""
import logging
from typing import List, Optional, Tuple

import duckdb
import pandas as pd

from ._utils import (
    _new_conn, _preview_table_dict, _query, _register, _table_schema_str,
)
from .base import DataSource

log = logging.getLogger(__name__)

# Safety cap: never pull more than this many rows from a single source query
# into memory. Analysts can still aggregate first, then materialise.
_MAX_FETCH_ROWS = 200_000


class MaxComputeDataSource(DataSource):
    """Connect to a MaxCompute project (the engine behind Alibaba DataWorks)."""

    def __init__(
        self,
        access_id: str,
        access_key: str,
        project: str,
        endpoint: str,
        display_name: str = "",
    ):
        from odps import ODPS

        self._odps = ODPS(
            access_id=access_id,
            secret_access_key=access_key,
            project=project,
            endpoint=endpoint,
        )
        # Fail fast on bad credentials / unreachable endpoint.
        self._odps.get_project()

        self.name = display_name or f"MaxCompute/{project}"
        self._project = project

        # DuckDB cache for materialised analysis tables.
        self._cache_conn: Optional[duckdb.DuckDBPyConnection] = None
        self._cache_tables: set = set()

    # ── schema / introspection ──────────────────────────────────────────────

    def get_schema(self) -> str:
        parts: List[str] = []
        try:
            for i, table in enumerate(self._odps.list_tables()):
                if i >= 50:
                    break
                try:
                    cols = table.table_schema.columns
                    col_lines = [f"  {c.name}  {c.type}" for c in cols]
                    parts.append(f"Table: {table.name}\n" + "\n".join(col_lines))
                except Exception:
                    parts.append(f"Table: {table.name}  (schema unavailable)")
        except Exception as exc:
            log.warning("[MaxCompute] list_tables failed: %s", exc)

        if self._cache_conn:
            for t in sorted(self._cache_tables):
                rows = self._cache_conn.execute(
                    f'SELECT COUNT(*) FROM "{t}"'
                ).fetchone()[0]
                parts.append(_table_schema_str(self._cache_conn, t, rows))

        return "\n\n".join(parts) if parts else "No tables found."

    def list_tables(self) -> List[str]:
        try:
            tables = [t.name for t in self._odps.list_tables()]
        except Exception:
            tables = []
        for t in sorted(self._cache_tables):
            if t not in tables:
                tables.append(t)
        return tables

    # ── query ─────────────────────────────────────────────────────────────────

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        # Runtime analysis tables live in DuckDB — route those there.
        if self._cache_conn and any(t in sql for t in self._cache_tables):
            return _query(self._cache_conn, sql)
        try:
            with self._odps.execute_sql(sql).open_reader(tunnel=True) as reader:
                df = reader.to_pandas(limit=_MAX_FETCH_ROWS)
            return df, ""
        except Exception as exc:
            return pd.DataFrame(), str(exc)

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
        if self._cache_conn is None:
            self._cache_conn = _new_conn()
        if _df is not None:
            _register(self._cache_conn, table_name, _df)
            rows = len(_df)
        else:
            df, err = self.execute_query(sql)
            if err:
                return f"Error building analysis table: {err}"
            _register(self._cache_conn, table_name, df)
            rows = len(df)
        self._cache_tables.add(table_name)
        return _table_schema_str(self._cache_conn, table_name, rows)

    # ── preview ─────────────────────────────────────────────────────────────

    def get_preview(self) -> List[dict]:
        result: List[dict] = []
        if self._cache_conn:
            for t in sorted(self._cache_tables):
                try:
                    cols = [r[0] for r in self._cache_conn.execute(f'DESCRIBE "{t}"').fetchall()]
                    total = self._cache_conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    result.append({"name": f"[分析表] {t}", "columns": cols, "total_rows": total})
                except Exception:
                    continue
        try:
            for i, table in enumerate(self._odps.list_tables()):
                if i >= 20:
                    break
                try:
                    cols = [c.name for c in table.table_schema.columns]
                except Exception:
                    cols = []
                result.append({"name": table.name, "columns": cols, "total_rows": None})
        except Exception as exc:
            log.warning("[MaxCompute] get_preview failed: %s", exc)
        return result

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        if self._cache_conn and table_name.startswith("[分析表] "):
            real = table_name[len("[分析表] "):]
            return _preview_table_dict(self._cache_conn, real, table_name, max_rows)
        df, err = self.execute_query(f"SELECT * FROM {table_name} LIMIT {max_rows}")
        if err:
            return {"name": table_name, "columns": [], "rows": [], "total_rows": None, "error": err}
        rows = [["" if v is None else str(v) for v in row] for row in df.itertuples(index=False)]
        return {"name": table_name, "columns": list(df.columns),
                "rows": rows, "total_rows": None}
