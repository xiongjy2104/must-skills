#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SelectDBDataSource — Alibaba Cloud SelectDB (Apache Doris) over MySQL protocol.

SelectDB Cloud exposes a MySQL-compatible endpoint (default query port 9030),
so we reuse the battle-tested SQLDataSource (SQLAlchemy + pymysql) and only add
a small helper that assembles the connection string from discrete fields. This
keeps the DuckDB analysis-cache behaviour identical to every other SQL source.
"""
from urllib.parse import quote_plus

from .sql import SQLDataSource

# SelectDB / Doris MySQL-protocol query port.
DEFAULT_PORT = 9030


def build_connection_string(
    host: str, user: str, password: str, database: str,
    port: int = DEFAULT_PORT,
) -> str:
    """Assemble a `mysql+pymysql://` URL with the password safely URL-encoded."""
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port or DEFAULT_PORT}/{database}"
    )


class SelectDBDataSource(SQLDataSource):
    """SelectDB Cloud connector.

    Thin subclass of SQLDataSource: it just builds the MySQL connection string
    from SelectDB-style fields. All schema / query / analysis-cache logic is
    inherited unchanged.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str,
        port: int = DEFAULT_PORT,
        display_name: str = "",
    ):
        conn_str = build_connection_string(host, user, password, database, port)
        super().__init__(conn_str, display_name or f"SelectDB/{database}")
