"""Data source connectors.

Public API — import these from `data.sources` (or via the legacy
`data.connector` shim which re-exports the same names):

    DataSource              — abstract base class
    ExcelDataSource         — .xlsx / .xls via calamine (Rust) → DuckDB
    CSVDataSource           — .csv via DuckDB read_csv_auto
    SQLDataSource           — any SQLAlchemy-supported DB + DuckDB analysis cache
    GoogleSheetsDataSource  — service-account JSON → all worksheets
    HTTPAPIDataSource       — JSON/CSV REST endpoint → DataFrame
    MaxComputeDataSource    — Alibaba DataWorks backend (MaxCompute/ODPS) → DuckDB
    SelectDBDataSource      — Alibaba SelectDB Cloud (MySQL protocol) + DuckDB cache
    OSSDataSource           — Excel/CSV object in Alibaba OSS → DuckDB
    LarkSheetsDataSource    — Lark online spreadsheet via Lark MCP → DuckDB
    MAX_DISPLAY_ROWS        — preview row cap surfaced to the LLM
"""
from .base        import DataSource, MAX_DISPLAY_ROWS
from .excel       import ExcelDataSource
from .csv         import CSVDataSource
from .sql         import SQLDataSource
from .gsheets     import GoogleSheetsDataSource
from .http        import HTTPAPIDataSource
from .maxcompute  import MaxComputeDataSource
from .selectdb    import SelectDBDataSource
from .oss         import OSSDataSource
from .lark_sheets import LarkSheetsDataSource

__all__ = [
    "DataSource",
    "MAX_DISPLAY_ROWS",
    "ExcelDataSource",
    "CSVDataSource",
    "SQLDataSource",
    "GoogleSheetsDataSource",
    "HTTPAPIDataSource",
    # Alibaba Cloud / Lark custom sources
    "MaxComputeDataSource",
    "SelectDBDataSource",
    "OSSDataSource",
    "LarkSheetsDataSource",
]
