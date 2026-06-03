"""Blueprint: data source management — upload Excel/CSV, connect SQL DB."""
import logging
import traceback
import uuid
import os
import re
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

from .state import session_manager, datasource_config_manager
from data.connector import (
    ExcelDataSource, CSVDataSource, SQLDataSource, GoogleSheetsDataSource,
    HTTPAPIDataSource, MaxComputeDataSource, SelectDBDataSource,
    OSSDataSource, LarkSheetsDataSource,
)

log = logging.getLogger(__name__)

bp = Blueprint("datasource", __name__)

# 自动识别环境，Vercel 用 /tmp，本地用项目目录
if os.environ.get("VERCEL"):
    UPLOAD_DIR = Path("/tmp/uploads")
else:
    UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXTS = {".xlsx", ".xls", ".csv"}


def _allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTS


def _friendly_conn_error(exc: Exception, service: str) -> str:
    """Translate a low-level connection exception into a user-readable message.

    `service` is a short label like 'Google Sheets' / '外部 API' / '数据库'.
    Falls back to the raw message when the error is not a known network case.
    """
    # Walk the exception cause chain so a wrapped error is still recognised.
    chain = []
    cur: BaseException | None = exc
    seen = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    text = "  ".join(f"{type(c).__name__}: {c}" for c in chain).lower()

    # — Network unreachable / connection reset (proxy, GFW, offline) —
    if any(k in text for k in (
        "10054", "connection aborted", "connection reset", "connectionreseterror",
        "connection refused", "10061", "max retries", "failed to establish",
        "name or service not known", "getaddrinfo failed", "11001",
        "transporterror", "ssl", "handshake", "timed out", "timeout",
        "remotedisconnected", "connectionerror",
    )):
        return (
            f"无法连接「{service}」：网络请求被中断或超时。"
            f"请检查网络是否可正常访问目标服务"
            + ("（Google 服务在部分网络下需要代理）" if "google" in service.lower() else "")
            + "，确认代理 / VPN 已开启且 Python 进程已走代理后重试。"
        )
    # — Authentication / authorization —
    if any(k in text for k in (
        "401", "403", "unauthorized", "forbidden", "permission",
        "invalid_grant", "invalid_client", "authentication",
        "access_denied", "credential",
    )):
        return (
            f"{service} 认证失败：凭证无效或没有访问权限。"
            "请检查服务账号 / 密钥是否正确，以及该账号是否已被授权访问目标资源。"
        )
    # — Not found —
    if any(k in text for k in ("404", "not found", "does not exist")):
        return f"{service} 目标资源不存在：请检查 URL / ID / 表名是否正确。"

    # — Unknown — keep the raw message but keep it short —
    raw = str(exc).strip() or type(exc).__name__
    if len(raw) > 200:
        raw = raw[:200] + "…"
    return f"{service} 连接失败：{raw}"


def _encode_db_password(conn_str: str) -> str:
    """对连接字符串中的密码部分做 URL 编码，处理 @ # 等特殊字符。"""
    # 匹配 scheme://user:password@host 格式，密码可能含多个 @
    # 贪婪匹配密码部分（.+），确保最后一个 @ 才是 host 分隔符
    m = re.match(r'^([a-zA-Z][a-zA-Z0-9+\-.]*://[^:@/]+):(.+)@([^@].*)', conn_str)
    if not m:
        return conn_str
    prefix, password, rest = m.group(1), m.group(2), m.group(3)
    # 若密码已含 % 编码则跳过，避免二次编码
    if re.search(r'%[0-9A-Fa-f]{2}', password):
        return conn_str
    encoded = quote(password, safe='-._~!*')
    return f"{prefix}:{encoded}@{rest}"


@bp.post("/api/session/<sid>/upload")
def upload_file(sid: str):
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    f = request.files["file"]
    if not f.filename or not _allowed(f.filename):
        return jsonify({"error": "仅支持 .xlsx / .xls / .csv 文件"}), 400

    display_name = f.filename  # keep original (may contain CJK/unicode)
    ext = Path(f.filename).suffix.lower()
    safe_stem = secure_filename(f.filename)
    safe_name = (safe_stem if safe_stem else f"upload_{uuid.uuid4().hex[:8]}{ext}")
    save_path = UPLOAD_DIR / f"{sid[:8]}_{uuid.uuid4().hex[:6]}_{safe_name}"
    f.save(str(save_path))

    log.info("[upload] saved → %s  (display: %s)", save_path, display_name)

    try:
        log.info("[upload] building DataSource …")
        if ext == ".csv":
            source = CSVDataSource(str(save_path), display_name)
        else:
            source = ExcelDataSource(str(save_path), display_name)

        log.info("[upload] DataSource ready, fetching schema …")
        schema = source.get_schema()
        log.info("[upload] schema OK:\n%s", schema)

        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        return jsonify({"ok": True, "source_name": display_name,
                        "schema_preview": schema})
    except Exception as exc:
        log.error("[upload] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": f"文件解析失败: {exc}"}), 400


@bp.post("/api/session/<sid>/connect-db")
def connect_db(sid: str):
    d = request.json or {}
    conn_str     = (d.get("connection_string") or "").strip()
    display_name = (d.get("name") or "").strip()
    # Use saved config if field left blank
    if not conn_str:
        saved = datasource_config_manager.get("sql")
        conn_str = (saved or {}).get("connection_string", "")
    if not conn_str:
        return jsonify({"error": "连接字符串不能为空"}), 400
    conn_str = _encode_db_password(conn_str)
    try:
        source = SQLDataSource(conn_str, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("sql", {
            "connection_string": conn_str, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-db] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "数据库")}), 400


@bp.get("/api/session/<sid>/preview")
def preview_data(sid: str):
    """Return table metadata only (name / columns / total_rows). No row data — fast."""
    sess = session_manager.get(sid)
    if not sess or not sess.data_source:
        return jsonify({"error": "no data source"}), 404
    tables = sess.data_source.get_preview()
    return jsonify({"source_name": sess.data_source.name, "tables": tables})


@bp.get("/api/session/<sid>/preview-table")
def preview_table(sid: str):
    """Return row data for a single table, fetched on demand by the frontend."""
    from flask import request as _req
    sess = session_manager.get(sid)
    if not sess or not sess.data_source:
        return jsonify({"error": "no data source"}), 404
    table_name = _req.args.get("table", "")
    if not table_name:
        return jsonify({"error": "missing table parameter"}), 400
    data = sess.data_source.get_preview_table(table_name, max_rows=100)
    return jsonify(data)


@bp.delete("/api/session/<sid>/datasource")
def disconnect_source(sid: str):
    sess = session_manager.get_or_create(sid)
    sess.data_source = None
    return jsonify({"ok": True})


@bp.post("/api/session/<sid>/connect-gsheets")
def connect_gsheets(sid: str):
    import json as _json
    d = request.json or {}
    creds_raw = d.get("creds_json", "")
    spreadsheet = (d.get("spreadsheet") or "").strip()
    display_name = (d.get("name") or "").strip()

    # Use saved creds if field left blank
    if not creds_raw:
        saved = datasource_config_manager.get("gsheets")
        creds_raw = (saved or {}).get("creds_json", "")
    if not spreadsheet:
        saved = datasource_config_manager.get("gsheets")
        spreadsheet = (saved or {}).get("spreadsheet", "")

    if not creds_raw:
        return jsonify({"error": "服务账号 JSON 不能为空"}), 400
    if not spreadsheet:
        return jsonify({"error": "电子表格 URL 或 ID 不能为空"}), 400

    try:
        creds_dict = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw
    except Exception:
        return jsonify({"error": "服务账号 JSON 格式无效"}), 400

    try:
        source = GoogleSheetsDataSource(creds_dict, spreadsheet, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("gsheets", {
            "creds_json": creds_raw if isinstance(creds_raw, str) else _json.dumps(creds_raw),
            "spreadsheet": spreadsheet, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-gsheets] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "Google Sheets")}), 400


@bp.post("/api/session/<sid>/connect-api")
def connect_api(sid: str):
    d = request.json or {}
    url = (d.get("url") or "").strip()
    auth_type = (d.get("auth_type") or "none").strip()
    auth_value = (d.get("auth_value") or "").strip()
    display_name = (d.get("name") or "").strip()

    # Fall back to saved config for blank fields
    saved = datasource_config_manager.get("api") or {}
    if not url:
        url = saved.get("url", "")
    if not url:
        return jsonify({"error": "API URL 不能为空"}), 400
    if not auth_type or auth_type == "none":
        auth_type = saved.get("auth_type", "none")
    if not auth_value:
        auth_value = saved.get("auth_value", "")
    if auth_type not in ("none", "bearer", "api_key"):
        return jsonify({"error": "认证方式无效，支持: none / bearer / api_key"}), 400

    try:
        source = HTTPAPIDataSource(url, auth_type, auth_value, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("api", {
            "url": url, "auth_type": auth_type,
            "auth_value": auth_value, "name": display_name
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-api] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "外部 API")}), 400


@bp.get("/api/datasource-configs")
def list_datasource_configs():
    return jsonify(datasource_config_manager.list_public())


@bp.delete("/api/datasource-configs/<ds_type>")
def delete_datasource_config(ds_type: str):
    datasource_config_manager.delete(ds_type)
    return jsonify({"ok": True})


# ── Alibaba Cloud / Lark custom data sources ──────────────────────────────────

@bp.post("/api/session/<sid>/connect-maxcompute")
def connect_maxcompute(sid: str):
    """Connect a MaxCompute project (the engine behind Alibaba DataWorks)."""
    d = request.json or {}
    saved = datasource_config_manager.get("maxcompute") or {}
    access_id    = (d.get("access_id") or "").strip()    or saved.get("access_id", "")
    access_key   = (d.get("access_key") or "").strip()   or saved.get("access_key", "")
    project      = (d.get("project") or "").strip()      or saved.get("project", "")
    endpoint     = (d.get("endpoint") or "").strip()     or saved.get("endpoint", "")
    display_name = (d.get("name") or "").strip()

    missing = [k for k, v in {
        "AccessKey ID": access_id, "AccessKey Secret": access_key,
        "项目名": project, "Endpoint": endpoint,
    }.items() if not v]
    if missing:
        return jsonify({"error": f"以下字段不能为空：{', '.join(missing)}"}), 400

    try:
        source = MaxComputeDataSource(access_id, access_key, project, endpoint, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("maxcompute", {
            "access_id": access_id, "access_key": access_key,
            "project": project, "endpoint": endpoint, "name": display_name,
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-maxcompute] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "MaxCompute / DataWorks")}), 400


@bp.post("/api/session/<sid>/connect-selectdb")
def connect_selectdb(sid: str):
    """Connect a SelectDB Cloud warehouse over the MySQL protocol."""
    d = request.json or {}
    saved = datasource_config_manager.get("selectdb") or {}
    host         = (d.get("host") or "").strip()     or saved.get("host", "")
    port         = d.get("port") or saved.get("port") or 9030
    user         = (d.get("user") or "").strip()     or saved.get("user", "")
    password     = (d.get("password") or "").strip() or saved.get("password", "")
    database     = (d.get("database") or "").strip() or saved.get("database", "")
    display_name = (d.get("name") or "").strip()

    if not host or not user or not database:
        return jsonify({"error": "host / user / database 不能为空"}), 400
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 9030

    try:
        source = SelectDBDataSource(host, user, password, database, port, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("selectdb", {
            "host": host, "port": port, "user": user,
            "password": password, "database": database, "name": display_name,
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-selectdb] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "SelectDB")}), 400


@bp.post("/api/session/<sid>/connect-oss")
def connect_oss(sid: str):
    """Load an Excel/CSV object stored in an Alibaba Cloud OSS bucket."""
    d = request.json or {}
    saved = datasource_config_manager.get("oss") or {}
    endpoint     = (d.get("endpoint") or "").strip()          or saved.get("endpoint", "")
    bucket       = (d.get("bucket") or "").strip()            or saved.get("bucket", "")
    object_key   = (d.get("object_key") or "").strip()        or saved.get("object_key", "")
    access_id    = (d.get("access_key_id") or "").strip()     or saved.get("access_key_id", "")
    access_secret= (d.get("access_key_secret") or "").strip() or saved.get("access_key_secret", "")
    display_name = (d.get("name") or "").strip()

    if not all([endpoint, bucket, object_key, access_id, access_secret]):
        return jsonify({"error": "endpoint / bucket / object_key / AccessKey 不能为空"}), 400

    try:
        source = OSSDataSource(endpoint, bucket, object_key, access_id, access_secret, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("oss", {
            "endpoint": endpoint, "bucket": bucket, "object_key": object_key,
            "access_key_id": access_id, "access_key_secret": access_secret,
            "name": display_name,
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-oss] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "OSS")}), 400


@bp.post("/api/session/<sid>/connect-lark")
def connect_lark(sid: str):
    """Load a Lark online spreadsheet through the Lark MCP server."""
    d = request.json or {}
    saved = datasource_config_manager.get("lark") or {}
    server_id    = (d.get("server_id") or "").strip()         or saved.get("server_id", "")
    token        = (d.get("spreadsheet_token") or "").strip() or saved.get("spreadsheet_token", "")
    read_tool    = (d.get("read_tool") or "").strip()         or saved.get("read_tool", "")
    display_name = (d.get("name") or "").strip()
    ranges_raw   = d.get("ranges") or saved.get("ranges") or []
    if isinstance(ranges_raw, str):
        ranges = [r.strip() for r in ranges_raw.split(",") if r.strip()]
    else:
        ranges = [str(r).strip() for r in ranges_raw if str(r).strip()]

    if not server_id or not token:
        return jsonify({"error": "Lark MCP 服务器 ID 和电子表格 token 不能为空"}), 400

    try:
        source = LarkSheetsDataSource(server_id, token, ranges, read_tool, display_name)
        sess = session_manager.get_or_create(sid)
        sess.data_source = source
        datasource_config_manager.save("lark", {
            "server_id": server_id, "spreadsheet_token": token,
            "read_tool": read_tool, "ranges": ranges, "name": display_name,
        })
        return jsonify({"ok": True, "source_name": source.name,
                        "schema_preview": source.get_schema()})
    except Exception as exc:
        log.error("[connect-lark] FAILED: %s\n%s", exc, traceback.format_exc())
        return jsonify({"error": _friendly_conn_error(exc, "Lark 在线表格")}), 400

