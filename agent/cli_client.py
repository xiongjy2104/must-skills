"""
Subprocess wrapper: makes Claude Code CLI look like an OpenAI streaming client.

Tool calling is prompt-based: full conversation + tool schemas are serialized
into the prompt; Claude is instructed to emit <tool_call>{...}</tool_call> tags;
we parse these and yield fake OpenAI-format streaming chunks so agent.py works
unchanged.

Authentication: the CLI uses whatever auth is already configured on the machine
(`claude login` / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY).
"""
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

log = logging.getLogger(__name__)

# ── Fake OpenAI response objects ──────────────────────────────────────────────

@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _FnCall:
    name: str = ""
    arguments: str = ""


@dataclass
class _ToolCallDelta:
    index: int = 0
    id: str = ""
    function: _FnCall = field(default_factory=_FnCall)


@dataclass
class _Delta:
    content: Optional[str] = None
    tool_calls: Optional[List[_ToolCallDelta]] = None
    reasoning_content: Optional[str] = None


@dataclass
class _Choice:
    delta: _Delta = field(default_factory=_Delta)
    finish_reason: Optional[str] = None


@dataclass
class _Chunk:
    choices: List[_Choice] = field(default_factory=list)
    usage: Optional[_Usage] = None


# ── Prompt serialization ──────────────────────────────────────────────────────

_TOOL_CALL_INSTRUCTIONS = """\
=== Tool Calling Instructions ===
When you need to call a tool, output EXACTLY this format on its own line and
nothing else after it — wait for the result before continuing:

<tool_call>{"name": "TOOL_NAME", "id": "tc_UNIQUE", "arguments": {JSON_ARGS}}</tool_call>

For a plain text response (no tool needed), just write normally.
=== End Instructions ===
"""


def _serialize(messages: List[Dict], tools: List[Dict]) -> tuple[str, str]:
    """Return (system_text, conversation_prompt) ready to pass to the CLI."""
    system_parts: List[str] = []
    conv_parts: List[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "system":
            text = content if isinstance(content, str) else " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )
            system_parts.append(text)

        elif role == "user":
            text = content if isinstance(content, str) else " ".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in content
            )
            conv_parts.append(f"[User]\n{text}\n")

        elif role == "assistant":
            text = ""
            if isinstance(content, list):
                text = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
            elif content:
                text = str(content)

            lines: List[str] = []
            if text:
                lines.append(text)
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function", {})
                try:
                    args_obj = json.loads(fn.get("arguments", "{}"))
                except Exception:
                    args_obj = {}
                tc_json = json.dumps(
                    {"name": fn.get("name", ""), "id": tc.get("id", "tc_?"), "arguments": args_obj},
                    ensure_ascii=False,
                )
                lines.append(f"<tool_call>{tc_json}</tool_call>")
            conv_parts.append(f"[Assistant]\n{chr(10).join(lines)}\n")

        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            conv_parts.append(f"[Tool result for {tc_id}]\n{content}\n")

    system_text = "\n\n".join(system_parts)

    if tools:
        tool_lines = ["=== Available Tools ==="]
        for t in tools:
            fn = t.get("function", {})
            tool_lines.append(f"Tool: {fn.get('name', '')}")
            tool_lines.append(f"Description: {fn.get('description', '')}")
            params = fn.get("parameters", {})
            if params:
                tool_lines.append(f"Parameters: {json.dumps(params, ensure_ascii=False)}")
            tool_lines.append("")
        tool_lines.append("=== End Tools ===")
        system_text = system_text + "\n\n" + _TOOL_CALL_INSTRUCTIONS + "\n" + "\n".join(tool_lines)

    return system_text.strip(), "\n".join(conv_parts).strip()


# ── Subprocess call ───────────────────────────────────────────────────────────

_TC_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _call_cli(cli: str, system: str, prompt: str, timeout: int = 300) -> str:
    """Call `claude -p` and return the raw result text."""
    cmd: List[str] = [cli, "--print", "--output-format", "json", "--no-session-persistence"]
    if system:
        cmd += ["--system-prompt", system]

    env = {**os.environ}  # inherit PATH, HOME, CLAUDE_CODE_OAUTH_TOKEN, etc.

    log.info("[cli_client] invoking CLI (prompt %d chars)", len(prompt))
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "[CLI 调用超时，请缩短对话或重试]"
    except FileNotFoundError:
        return f"[找不到 claude CLI 可执行文件: {cli!r}，请确认已安装 Claude Code CLI 并登录]"
    except Exception as exc:
        return f"[CLI 调用异常: {exc}]"

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[:400]
        return f"[CLI 返回错误 (code {proc.returncode}): {err}]"

    try:
        data = json.loads(proc.stdout)
        return data.get("result", "") or ""
    except Exception:
        return proc.stdout or ""


# ── Fake completions that agent.py can iterate ────────────────────────────────

class _Completions:
    def __init__(self, cli_path: str):
        self._cli = cli_path

    def create(
        self, *, model: str, messages: List[Dict],
        tools=None, tool_choice=None,
        temperature=None, max_tokens=None,
        stream: bool = False, stream_options=None,
        **kwargs,
    ) -> Iterator[_Chunk]:
        return self._stream(messages, tools or [])

    def _stream(self, messages: List[Dict], tools: List[Dict]) -> Iterator[_Chunk]:
        system, prompt = _serialize(messages, tools)
        raw = _call_cli(self._cli, system, prompt)

        tc_matches = _TC_RE.findall(raw)

        if tc_matches:
            # Text before tool calls (if any)
            text_only = _TC_RE.sub("", raw).strip()
            if text_only:
                yield _Chunk(choices=[_Choice(delta=_Delta(content=text_only))])

            for i, tc_str in enumerate(tc_matches):
                try:
                    tc_data = json.loads(tc_str.strip())
                    tcd = _ToolCallDelta(
                        index=i,
                        id=tc_data.get("id", f"tc_{uuid.uuid4().hex[:8]}"),
                        function=_FnCall(
                            name=tc_data.get("name", ""),
                            arguments=json.dumps(tc_data.get("arguments", {}), ensure_ascii=False),
                        ),
                    )
                    yield _Chunk(choices=[_Choice(delta=_Delta(tool_calls=[tcd]))])
                except Exception as exc:
                    log.warning("[cli_client] bad tool_call JSON: %s | %s", tc_str[:120], exc)

            yield _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="tool_calls")])

        else:
            # Stream text in small chunks for responsive UI
            chunk_size = 40
            for i in range(0, len(raw), chunk_size):
                yield _Chunk(choices=[_Choice(delta=_Delta(content=raw[i : i + chunk_size]))])

            yield _Chunk(
                choices=[_Choice(delta=_Delta(), finish_reason="stop")],
                usage=_Usage(
                    prompt_tokens=max(1, len(prompt) // 4),
                    completion_tokens=max(1, len(raw) // 4),
                    total_tokens=max(1, (len(prompt) + len(raw)) // 4),
                ),
            )


class _Chat:
    def __init__(self, cli_path: str):
        self.completions = _Completions(cli_path)


class ClaudeCliClient:
    """Drop-in replacement for openai.OpenAI backed by Claude Code CLI subprocess."""

    def __init__(self, cli_path: str = "claude"):
        resolved = shutil.which(cli_path) or cli_path
        log.info("[cli_client] resolved CLI → %s", resolved)
        self.chat = _Chat(resolved)
