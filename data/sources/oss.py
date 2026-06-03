#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OSSDataSource — read Excel/CSV objects from Alibaba Cloud OSS.

OSS is object storage, not a query engine, so this source downloads the target
object to a temp file and then *delegates* to the existing Excel / CSV sources
(which load it into DuckDB). All schema / query / preview behaviour is therefore
identical to a locally uploaded file — we only add the download step.
"""
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from .base import DataSource
from .csv import CSVDataSource
from .excel import ExcelDataSource

log = logging.getLogger(__name__)

_SUPPORTED_EXTS = {".xlsx", ".xls", ".csv"}


class OSSDataSource(DataSource):
    """Load a single Excel/CSV object stored in an Alibaba Cloud OSS bucket."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        object_key: str,
        access_key_id: str,
        access_key_secret: str,
        display_name: str = "",
    ):
        import oss2

        ext = Path(object_key).suffix.lower()
        if ext not in _SUPPORTED_EXTS:
            raise ValueError(
                f"OSS 对象 '{object_key}' 不受支持，仅支持 {sorted(_SUPPORTED_EXTS)}"
            )

        auth = oss2.Auth(access_key_id, access_key_secret)
        oss_bucket = oss2.Bucket(auth, endpoint, bucket)

        # Download to a temp file (cleaned up only on process exit — the inner
        # DuckDB source may lazily reference the path for CSV).
        tmp_dir = tempfile.mkdtemp(prefix="oss_")
        local_path = os.path.join(tmp_dir, Path(object_key).name)
        log.info("[OSS] downloading oss://%s/%s → %s", bucket, object_key, local_path)
        oss_bucket.get_object_to_file(object_key, local_path)

        self.name = display_name or f"OSS/{bucket}/{object_key}"
        self._local_path = local_path

        # Delegate to the matching file source.
        if ext == ".csv":
            self._inner: DataSource = CSVDataSource(local_path, self.name)
        else:
            self._inner = ExcelDataSource(local_path, self.name)

    # ── delegate the whole DataSource contract to the inner file source ──────

    def get_schema(self) -> str:
        return self._inner.get_schema()

    def execute_query(self, sql: str) -> Tuple[pd.DataFrame, str]:
        return self._inner.execute_query(sql)

    def create_analysis_table(
        self, sql: str, table_name: str = "analysis_data", _df=None
    ) -> str:
        return self._inner.create_analysis_table(sql, table_name, _df)

    def list_tables(self) -> List[str]:
        return self._inner.list_tables()

    def get_preview(self) -> List[dict]:
        return self._inner.get_preview()

    def get_preview_table(self, table_name: str, max_rows: int = 100) -> dict:
        return self._inner.get_preview_table(table_name, max_rows)
