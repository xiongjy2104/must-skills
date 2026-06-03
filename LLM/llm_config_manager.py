#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API Key 配置管理
支持 DeepSeek、OpenAI、Claude 等多个 LLM 提供商
支持用户自定义 OpenAI SDK 兼容的模型
"""
import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)

CONFIG_DIR = Path("/tmp/LLM") if os.environ.get("VERCEL") else Path(__file__).parent
if os.environ.get("VERCEL"):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

LLM_CONFIG_FILE = CONFIG_DIR / "llm_config.json"

@dataclass
class LLMConfig:
    """LLM 配置（一条 = 一个账号）"""
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    enabled: bool = True
    is_custom: bool = False
    context_window: Optional[int] = None    # 上下文窗口（tokens）
    max_output_tokens: Optional[int] = None  # 最大输出（tokens）
    enable_thinking: bool = False            # 启用推理链（DeepSeek-R1 / Claude 3.7+）
    thinking_budget: int = 8000              # Claude extended thinking budget_tokens
    label: str = ""                          # 账号显示名（如 "企业版" / "Pro 版"）


class LLMConfigManager:
    """LLM 配置管理器"""

    DEFAULT_CONFIGS = {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "env_var": "DEEPSEEK_API_KEY",
            "is_custom": False,
            "context_window": 64000,
            "max_output_tokens": 8192,
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "env_var": "OPENAI_API_KEY",
            "is_custom": False,
            "context_window": 128000,
            "max_output_tokens": 16384,
        },
        "claude": {
            # Anthropic's OpenAI-compatible endpoint (used via the openai SDK).
            "base_url": "https://api.anthropic.com/v1/",
            "model": "claude-sonnet-4-6",
            "env_var": "ANTHROPIC_API_KEY",
            "is_custom": False,
            "context_window": 200000,
            "max_output_tokens": 64000,
        },
    }

    def __init__(self, load_from_env: bool = False):
        """
        初始化配置管理器
        load_from_env=False: 默认不从环境变量回灌，避免“删了又出现”
        """
        self.configs: Dict[str, LLMConfig] = {}
        # provider → 当前选中的账号 config_id（多账号切换的指针）
        self._active: Dict[str, str] = {}
        self.load_configs(load_from_env=load_from_env)

    def load_configs(self, load_from_env: bool = False):
        """从文件加载配置。

        向后兼容旧格式：旧 JSON 形如 {provider: {...}}，每条即一个账号，
        其 config_id 等于 provider。新格式额外保存一个保留键 "__active__"，
        记录每个 provider 当前选中的账号。
        """
        self.configs = {}
        self._active = {}

        if LLM_CONFIG_FILE.exists():
            try:
                with open(LLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._active = dict(data.pop("__active__", {}) or {})
                    for config_id, config in data.items():
                        # 容错：忽略 dataclass 不认识的多余字段
                        known = {k: v for k, v in config.items()
                                 if k in LLMConfig.__dataclass_fields__}
                        self.configs[config_id] = LLMConfig(**known)
            except Exception as e:
                log.error("加载配置失败: %s", e)

        if load_from_env:
            self._load_from_env()

    # ── 多账号支持 ────────────────────────────────────────────────────────────

    def _accounts_of(self, provider: str) -> List[str]:
        """返回某 provider 下所有账号的 config_id 列表。"""
        return [cid for cid, c in self.configs.items() if c.provider == provider]

    def resolve(self, provider_or_id: str) -> Optional[str]:
        """把 provider 名或 config_id 解析成具体的账号 config_id。

        优先级（注意：首个账号的 config_id 与 provider 名相同，故活跃指针必须先查）：
        1. 该 provider 有活跃指针 → 用活跃账号；
        2. 传入值本身就是一个 config_id → 直接返回；
        3. 视为 provider 名 → 第一个启用的账号 / 任意账号。

        切换账号 = 设置活跃指针（set_active_account），因此「选哪个账号」
        全局生效，resolve(provider) 与 resolve(config_id) 结果一致。
        """
        active = self._active.get(provider_or_id)
        if active and active in self.configs:
            return active
        if provider_or_id in self.configs:
            return provider_or_id
        accounts = self._accounts_of(provider_or_id)
        enabled = [c for c in accounts if self.configs[c].enabled]
        if enabled:
            return enabled[0]
        return accounts[0] if accounts else None

    def add_account(
        self, provider: str, label: str, api_key: str,
        base_url: Optional[str] = None, model: Optional[str] = None,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False, thinking_budget: int = 8000,
        make_active: bool = True,
    ) -> tuple[bool, str]:
        """为某个 provider 新增一个账号（如同一家的企业版 / Pro 版）。"""
        if not provider or not provider.strip():
            return False, "provider 不能为空"
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空"
        provider = provider.strip()

        # 若该 provider 还没有任何账号，首个账号沿用 id==provider（兼容旧逻辑）。
        if not self._accounts_of(provider):
            config_id = provider
        else:
            n = 2
            while f"{provider}#{n}" in self.configs:
                n += 1
            config_id = f"{provider}#{n}"

        defaults = self.DEFAULT_CONFIGS.get(provider, {})
        self.configs[config_id] = LLMConfig(
            provider=provider,
            api_key=api_key.strip(),
            base_url=(base_url.strip() if base_url else defaults.get("base_url")),
            model=(model.strip() if model else defaults.get("model")),
            enabled=True,
            is_custom=defaults == {},  # provider 不在内置表里 → 视为自定义家族
            context_window=context_window if context_window is not None else defaults.get("context_window"),
            max_output_tokens=max_output_tokens if max_output_tokens is not None else defaults.get("max_output_tokens"),
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            label=label.strip() or config_id,
        )
        if make_active or provider not in self._active:
            self._active[provider] = config_id

        if self.save_configs():
            return True, f"账号「{label or config_id}」添加成功"
        del self.configs[config_id]
        return False, "保存配置失败"

    def set_active_account(self, provider: str, config_id: str) -> tuple[bool, str]:
        """切换某 provider 当前使用的账号。"""
        if config_id not in self.configs:
            return False, f"账号 '{config_id}' 不存在"
        if self.configs[config_id].provider != provider:
            return False, f"账号 '{config_id}' 不属于 provider '{provider}'"
        self._active[provider] = config_id
        if self.save_configs():
            return True, "已切换默认账号"
        return False, "保存失败"

    def delete_account(self, config_id: str) -> tuple[bool, str]:
        """删除一个账号。若删的是活跃账号，自动切到同 provider 的其它账号。"""
        cfg = self.configs.get(config_id)
        if not cfg:
            return False, f"账号 '{config_id}' 不存在"
        provider = cfg.provider
        del self.configs[config_id]
        if self._active.get(provider) == config_id:
            remaining = self._accounts_of(provider)
            if remaining:
                self._active[provider] = remaining[0]
            else:
                self._active.pop(provider, None)
        if self.save_configs():
            return True, f"账号 '{config_id}' 已删除"
        self.configs[config_id] = cfg
        return False, "删除失败"

    def list_accounts(self, provider: str) -> List[Dict[str, Any]]:
        """列出某 provider 下所有账号（不含明文 key）。"""
        active = self._active.get(provider)
        result = []
        for cid in self._accounts_of(provider):
            c = self.configs[cid]
            result.append({
                "config_id": cid,
                "label": c.label or cid,
                "model": c.model,
                "base_url": c.base_url,
                "enabled": c.enabled,
                "has_api_key": bool(c.api_key),
                "is_active": cid == active,
            })
        return result

    def _load_from_env(self):
        """从环境变量加载内置提供商配置（仅在显式开启时使用）"""
        for provider, defaults in self.DEFAULT_CONFIGS.items():
            env_var = defaults["env_var"]
            api_key = os.environ.get(env_var)

            if api_key and provider not in self.configs:
                self.configs[provider] = LLMConfig(
                    provider=provider,
                    api_key=api_key.strip(),
                    base_url=defaults.get("base_url"),
                    model=defaults.get("model"),
                    enabled=True,
                    is_custom=False
                )

    def save_configs(self):
        """保存配置到文件"""
        try:
            data = {
                config_id: asdict(config)
                for config_id, config in self.configs.items()
            }
            # 仅保留仍然有效的活跃指针
            active = {p: cid for p, cid in self._active.items() if cid in self.configs}
            if active:
                data["__active__"] = active
            with open(LLM_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            log.error("保存配置失败: %s", e)
            return False

    def add_custom_model(
        self, name: str, base_url: str, model_name: str, api_key: str,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False, thinking_budget: int = 8000,
    ) -> tuple[bool, str]:
        if not name or not name.strip():
            return False, "模型名称不能为空"
        if not base_url or not base_url.strip():
            return False, "API 调用链接不能为空"
        if not model_name or not model_name.strip():
            return False, "模型名称不能为空"
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空"

        provider_id = f"custom_{name.lower().replace(' ', '_')}"
        if provider_id in self.configs:
            return False, f"模型 '{name}' 已存在"

        self.configs[provider_id] = LLMConfig(
            provider=provider_id,
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            model=model_name.strip(),
            enabled=True,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        )

        if self.save_configs():
            return True, f"模型 '{name}' 添加成功"
        else:
            del self.configs[provider_id]
            return False, "保存配置失败"

    def set_config(
        self, provider: str, api_key: str,
        base_url: Optional[str] = None, model: Optional[str] = None,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False, thinking_budget: int = 8000,
    ) -> bool:
        """设置内置提供商配置"""
        if provider not in self.DEFAULT_CONFIGS:
            log.warning("不支持的提供商: %s", provider)
            return False

        if not api_key or not api_key.strip():
            log.warning("API Key 不能为空")
            return False

        defaults = self.DEFAULT_CONFIGS[provider]
        self.configs[provider] = LLMConfig(
            provider=provider,
            api_key=api_key.strip(),
            base_url=(base_url.strip() if base_url else defaults.get("base_url")),
            model=(model.strip() if model else defaults.get("model")),
            enabled=True,
            is_custom=False,
            context_window=context_window if context_window is not None else defaults.get("context_window"),
            max_output_tokens=max_output_tokens if max_output_tokens is not None else defaults.get("max_output_tokens"),
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        )

        # 关键修复：不再写 os.environ，避免进程内“复活”
        # os.environ[defaults["env_var"]] = api_key

        return self.save_configs()

    def clear_builtin_config(self, provider: str) -> tuple[bool, str]:
        """清空内置 provider 配置（删除文件中的配置，并清理进程环境变量）"""
        if provider not in self.DEFAULT_CONFIGS:
            return False, f"不支持的内置提供商: {provider}"

        self.configs.pop(provider, None)

        # 清理当前进程环境变量（即使你现在不写 env，也防历史残留）
        env_var = self.DEFAULT_CONFIGS[provider]["env_var"]
        os.environ.pop(env_var, None)

        if self.save_configs():
            return True, f"内置配置已清空: {provider}"
        return False, "保存配置失败"

    def get_config(self, provider: str) -> Optional[LLMConfig]:
        """按 config_id 或 provider 名取配置（provider 名 → 解析到活跃账号）。"""
        config_id = self.resolve(provider)
        return self.configs.get(config_id) if config_id else None

    def update_custom_model(
        self, provider: str, base_url: str, model_name: str, api_key: str,
        context_window: Optional[int] = None, max_output_tokens: Optional[int] = None,
        enable_thinking: bool = False, thinking_budget: int = 8000,
    ) -> tuple[bool, str]:
        """更新已有自定义模型配置"""
        if provider not in self.configs:
            return False, f"配置 '{provider}' 不存在"
        cfg = self.configs[provider]
        if not cfg.is_custom:
            return False, "只能编辑自定义模型"
        if not base_url or not base_url.strip():
            return False, "API Base URL 不能为空"
        if not model_name or not model_name.strip():
            return False, "Model ID 不能为空"
        # api_key 留空则保留旧值
        new_key = api_key.strip() if api_key and api_key.strip() else cfg.api_key
        old = self.configs[provider]
        self.configs[provider] = LLMConfig(
            provider=provider,
            api_key=new_key,
            base_url=base_url.strip(),
            model=model_name.strip(),
            enabled=cfg.enabled,
            is_custom=True,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        )
        if self.save_configs():
            return True, "配置已更新"
        self.configs[provider] = old
        return False, "保存失败"

    def delete_config(self, provider: str) -> tuple[bool, str]:
        """删除配置（仅自定义）"""
        if provider not in self.configs:
            return False, f"配置 '{provider}' 不存在"

        config = self.configs[provider]
        if not config.is_custom:
            return False, f"无法删除内置提供商 '{provider}'，请使用 clear_builtin_config"

        del self.configs[provider]
        if self.save_configs():
            return True, f"配置 '{provider}' 已删除"
        else:
            self.configs[provider] = config
            return False, "删除失败"

    def get_enabled_providers(self) -> List[str]:
        return [p for p, c in self.configs.items() if c.enabled]

    def get_custom_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "provider": provider,
                "name": config.model,
                "base_url": config.base_url,
                "enabled": config.enabled
            }
            for provider, config in self.configs.items()
            if config.is_custom
        ]

    def get_default_provider(self) -> Optional[str]:
        """返回默认使用的账号 config_id（按 provider 优先级解析到活跃账号）。"""
        priority = ["deepseek", "openai", "claude"]
        for provider in priority:
            cid = self.resolve(provider)
            if cid and self.configs[cid].enabled:
                return cid

        for config_id, config in self.configs.items():
            if config.is_custom and config.enabled:
                return config_id

        return None

    def list_configs(self) -> Dict[str, Any]:
        """注意：不返回 api_key 明文。键为 config_id（多账号时一个 provider 有多条）。"""
        result = {}
        for config_id, config in self.configs.items():
            result[config_id] = {
                "config_id": config_id,
                "provider": config.provider,
                "label": config.label or config_id,
                "base_url": config.base_url,
                "model": config.model,
                "enabled": config.enabled,
                "is_custom": config.is_custom,
                "has_api_key": bool(config.api_key),
                "context_window": config.context_window,
                "max_output_tokens": config.max_output_tokens,
                "enable_thinking": config.enable_thinking,
                "is_active": self._active.get(config.provider) == config_id,
            }
        return result

    def test_config(self, provider: str) -> Dict[str, Any]:
        config = self.get_config(provider)
        if not config:
            return {"success": False, "message": f"未找到 {provider} 的配置", "provider": provider}

        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            client.chat.completions.create(
                model=config.model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=10
            )
            return {"success": True, "message": "配置有效", "provider": provider, "model": config.model}
        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}", "provider": provider, "model": config.model}


_config_manager = None


def get_config_manager() -> LLMConfigManager:
    global _config_manager
    if _config_manager is None:
        # 默认禁用 env 回灌
        _config_manager = LLMConfigManager(load_from_env=False)
    return _config_manager


def get_llm_client(provider: Optional[str] = None):
    manager = get_config_manager()

    if provider is None:
        provider = manager.get_default_provider()
    if provider is None:
        raise ValueError("未配置任何 LLM 提供商")

    config = manager.get_config(provider)
    if not config:
        raise ValueError(f"未找到 {provider} 的配置")

    from openai import OpenAI
    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def get_llm_client_with_fallback(preferred_provider: Optional[str] = None):
    """
    Return (client, provider, config) trying preferred_provider first,
    then falling back through enabled providers in priority order.
    Raises ValueError only when all providers are exhausted.
    """
    import logging
    log = logging.getLogger(__name__)

    manager = get_config_manager()
    from openai import OpenAI

    # Build candidate list of concrete account config_ids: preferred first
    # (resolved to its active account), then priority providers, then any
    # other enabled account. This lets a provider with several accounts fall
    # back across providers — not across accounts of the same one — by default.
    candidates: List[str] = []

    def _add(cid: Optional[str]):
        if cid and cid in manager.configs and manager.configs[cid].enabled and cid not in candidates:
            candidates.append(cid)

    if preferred_provider:
        _add(manager.resolve(preferred_provider))

    for p in ["deepseek", "openai", "claude"]:
        _add(manager.resolve(p))

    # Append any other enabled account (covers extra accounts + custom models)
    for cid, cfg in manager.configs.items():
        if cfg.enabled:
            _add(cid)

    last_exc: Optional[Exception] = None
    for provider in candidates:
        config = manager.get_config(provider)
        if not config or not config.api_key:
            continue
        try:
            client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            # Lightweight probe — just instantiate, don't make a network call
            log.info("[llm] selected provider=%s model=%s", provider, config.model)
            return client, provider, config
        except Exception as exc:
            log.warning("[llm] provider %s unavailable: %s", provider, exc)
            last_exc = exc

    raise ValueError(f"所有 LLM 提供商均不可用。最后错误: {last_exc}")


if __name__ == "__main__":
    manager = get_config_manager()
    print("当前配置:")
    print(json.dumps(manager.list_configs(), indent=2, ensure_ascii=False))
    print(f"\n默认提供商: {manager.get_default_provider()}")
