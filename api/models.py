"""Blueprint: LLM model management — /api/models/*"""
from flask import Blueprint, request, jsonify
from .state import config_manager, session_manager

bp = Blueprint("models", __name__)


@bp.get("/api/models")
def list_models():
    return jsonify(config_manager.list_configs())


@bp.get("/api/models/defaults")
def model_defaults():
    return jsonify({
        k: {
            "base_url": v["base_url"],
            "model": v["model"],
            "context_window": v.get("context_window"),
            "max_output_tokens": v.get("max_output_tokens"),
        }
        for k, v in config_manager.DEFAULT_CONFIGS.items()
    })


@bp.post("/api/models/set-builtin")
def set_builtin():
    d = request.json or {}
    provider = d.get("provider", "").strip()
    api_key  = d.get("api_key", "").strip()
    base_url = d.get("base_url", "").strip() or None
    model    = d.get("model", "").strip() or None
    context_window    = _to_int(d.get("context_window"))
    max_output_tokens = _to_int(d.get("max_output_tokens"))
    enable_thinking   = bool(d.get("enable_thinking", False))
    thinking_budget   = _to_int(d.get("thinking_budget")) or 8000
    from LLM.llm_config_manager import LLMConfigManager
    no_api_key = LLMConfigManager.DEFAULT_CONFIGS.get(provider, {}).get("no_api_key", False)
    if not provider or (not no_api_key and not api_key):
        return jsonify({"error": "provider 和 api_key 不能为空"}), 400
    ok = config_manager.set_config(
        provider, api_key, base_url=base_url, model=model,
        context_window=context_window, max_output_tokens=max_output_tokens,
        enable_thinking=enable_thinking, thinking_budget=thinking_budget,
    )
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": f"不支持的内置提供商: {provider}"}), 400


@bp.post("/api/models/clear-builtin")
def clear_builtin():
    d = request.json or {}
    ok, msg = config_manager.clear_builtin_config(d.get("provider", "").strip())
    return jsonify({"ok": ok, "message": msg})


@bp.post("/api/models/add")
def add_model():
    d = request.json or {}
    ok, msg = config_manager.add_custom_model(
        name=d.get("name", ""),
        base_url=d.get("base_url", ""),
        model_name=d.get("model_name", ""),
        api_key=d.get("api_key", ""),
        context_window=_to_int(d.get("context_window")),
        max_output_tokens=_to_int(d.get("max_output_tokens")),
        enable_thinking=bool(d.get("enable_thinking", False)),
        thinking_budget=_to_int(d.get("thinking_budget")) or 8000,
    )
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


def _to_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "0") else None
    except (TypeError, ValueError):
        return None


@bp.post("/api/models/update")
def update_model():
    d = request.json or {}
    ok, msg = config_manager.update_custom_model(
        provider=d.get("provider", "").strip(),
        base_url=d.get("base_url", ""),
        model_name=d.get("model_name", ""),
        api_key=d.get("api_key", ""),
        context_window=_to_int(d.get("context_window")),
        max_output_tokens=_to_int(d.get("max_output_tokens")),
        enable_thinking=bool(d.get("enable_thinking", False)),
        thinking_budget=_to_int(d.get("thinking_budget")) or 8000,
    )
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


@bp.post("/api/models/delete")
def delete_model():
    d = request.json or {}
    ok, msg = config_manager.delete_config(d.get("provider", "").strip())
    if ok:
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400


@bp.post("/api/models/test")
def test_model():
    d = request.json or {}
    return jsonify(config_manager.test_config(d.get("provider", "")))


# ── Multi-account management ──────────────────────────────────────────────────

@bp.get("/api/models/accounts/<provider>")
def list_accounts(provider: str):
    """List every account configured for a provider (e.g. Claude 企业版 / Pro 版)."""
    return jsonify({"provider": provider, "accounts": config_manager.list_accounts(provider)})


@bp.post("/api/models/accounts/add")
def add_account():
    d = request.json or {}
    ok, msg = config_manager.add_account(
        provider=d.get("provider", "").strip(),
        label=d.get("label", "").strip(),
        api_key=d.get("api_key", ""),
        base_url=(d.get("base_url", "").strip() or None),
        model=(d.get("model", "").strip() or None),
        context_window=_to_int(d.get("context_window")),
        max_output_tokens=_to_int(d.get("max_output_tokens")),
        enable_thinking=bool(d.get("enable_thinking", False)),
        thinking_budget=_to_int(d.get("thinking_budget")) or 8000,
        make_active=bool(d.get("make_active", True)),
    )
    return (jsonify({"ok": True, "message": msg}) if ok
            else (jsonify({"error": msg}), 400))


@bp.post("/api/models/accounts/set-active")
def set_active_account():
    d = request.json or {}
    ok, msg = config_manager.set_active_account(
        d.get("provider", "").strip(), d.get("config_id", "").strip()
    )
    return (jsonify({"ok": True, "message": msg}) if ok
            else (jsonify({"error": msg}), 400))


@bp.post("/api/models/accounts/delete")
def delete_account():
    d = request.json or {}
    ok, msg = config_manager.delete_account(d.get("config_id", "").strip())
    return (jsonify({"ok": True, "message": msg}) if ok
            else (jsonify({"error": msg}), 400))


@bp.post("/api/session/<sid>/model")
def set_session_model(sid: str):
    d = request.json or {}
    provider = d.get("provider", "").strip()
    cfg = config_manager.get_config(provider)
    if not cfg:
        return jsonify({"error": f"未知的模型: {provider}"}), 400
    # If the selection is a specific account (config_id), make it the active
    # account for its provider so the switch takes effect globally.
    if provider in config_manager.configs:
        config_manager.set_active_account(cfg.provider, provider)
    sess = session_manager.get_or_create(sid)
    sess.model_provider = provider
    return jsonify({"ok": True})
