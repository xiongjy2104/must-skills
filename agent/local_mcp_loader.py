"""读取并归一化 Claude 各客户端的 MCP server 定义。

复用的本质是复用 server 的「定义」，而不是连到 Claude。stdio server 每个客户端
各自 fork 子进程，所以这里把定义读出来，由本平台自行拉起；远程 (url) server 则可
与 Claude 共享同一运行实例。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 在 Windows 上，这些命令通常是 .cmd 批处理 shim，必须经 `cmd /c` 才能被 spawn 拉起，
# 否则会报 [WinError 2] 找不到文件。直接 spawn npx/uvx 是 Windows 上最常见的坑。
_WINDOWS_WRAP = {"npx", "npm", "pnpm", "yarn", "bunx", "bun", "uvx", "uv", "node", "deno"}


@dataclass
class ServerSpec:
    """归一化后的单个 MCP server 定义。"""

    name: str
    transport: str  # "stdio" | "http" | "sse"
    # stdio
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # 远程
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_stdio(self) -> bool:
        return self.transport == "stdio"


def _default_config_paths() -> list[Path]:
    """各客户端默认配置位置，跨平台。靠前的优先级低，会被靠后的同名 server 覆盖。"""
    home = Path.home()
    paths: list[Path] = []

    if os.name == "nt":  # Windows
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        paths.append(appdata / "Claude" / "claude_desktop_config.json")  # Claude Desktop
    elif os.sys.platform == "darwin":  # type: ignore[attr-defined]
        paths.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:  # Linux
        paths.append(home / ".config" / "Claude" / "claude_desktop_config.json")

    paths.append(home / ".cursor" / "mcp.json")          # Cursor（用户级）
    paths.append(home / ".claude.json")                  # Claude Code（用户级，含 projects 嵌套）

    # 项目级（当前工作目录），优先级最高
    cwd = Path.cwd()
    paths.append(cwd / ".cursor" / "mcp.json")
    paths.append(cwd / ".mcp.json")                      # Claude Code 项目级

    return paths


def _extract_mcp_servers(raw: dict) -> dict:
    """从一份配置 JSON 中抽出 mcpServers 映射。

    Claude Desktop / Cursor / Claude Code 项目级 .mcp.json 都是顶层 mcpServers；
    Claude Code 用户级 ~/.claude.json 把它嵌在 projects.<绝对路径>.mcpServers 下。
    """
    servers: dict = dict(raw.get("mcpServers", {}))

    projects = raw.get("projects")
    if isinstance(projects, dict):
        # 合并所有 project 的 server；当前工作目录对应的 project 优先级更高
        cwd = str(Path.cwd())
        ordered = sorted(projects.items(), key=lambda kv: kv[0] == cwd)
        for _proj_path, proj_cfg in ordered:
            if isinstance(proj_cfg, dict):
                servers.update(proj_cfg.get("mcpServers", {}))
    return servers


def _normalize(name: str, entry: dict, wrap_windows: bool) -> Optional[ServerSpec]:
    """把一条原始配置归一化成 ServerSpec。无法识别的条目返回 None。"""
    # 传输类型：优先取显式字段，否则按字段推断
    transport = (entry.get("type") or entry.get("transport") or "").lower()
    if not transport:
        transport = "stdio" if entry.get("command") else ("http" if entry.get("url") else "")

    if transport == "stdio":
        command = entry.get("command")
        if not command:
            return None
        args = list(entry.get("args", []))
        env = dict(entry.get("env", {}))

        if wrap_windows and os.name == "nt" and command.lower() in _WINDOWS_WRAP:
            args = ["/c", command, *args]
            command = "cmd"

        return ServerSpec(name=name, transport="stdio", command=command, args=args, env=env)

    if transport in ("http", "streamable-http", "streamablehttp", "sse"):
        url = entry.get("url")
        if not url:
            return None
        norm = "sse" if transport == "sse" else "http"
        return ServerSpec(
            name=name,
            transport=norm,
            url=url,
            headers=dict(entry.get("headers", {})),
        )

    return None


def load_servers(
    config_paths: Optional[list[str | os.PathLike]] = None,
    wrap_windows: bool = True,
    skip_disabled: bool = True,
) -> dict[str, ServerSpec]:
    """加载并归一化所有 MCP server 定义。

    Args:
        config_paths: 自定义配置文件列表；为 None 时扫描各客户端默认位置。
        wrap_windows: 在 Windows 上自动用 `cmd /c` 包裹 npx/uvx 等命令。
        skip_disabled: 跳过被标记 disabled/enabled=false 的 server。

    Returns:
        {server_name: ServerSpec}。同名 server 后扫描到的覆盖先前的。
    """
    paths = [Path(p) for p in config_paths] if config_paths else _default_config_paths()

    merged: dict[str, ServerSpec] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 单份配置坏了不应拖垮整体
            continue

        for name, entry in _extract_mcp_servers(raw).items():
            if not isinstance(entry, dict):
                continue
            if skip_disabled and (entry.get("disabled") is True or entry.get("enabled") is False):
                continue
            spec = _normalize(name, entry, wrap_windows)
            if spec is not None:
                merged[name] = spec

    return merged
