"""Adaptador do Claude Code headless: roda `claude -p --output-format stream-json` e
traduz o NDJSON em eventos simples. Não conhece Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass

log = logging.getLogger("claude-bot")

# stream-json pode emitir linhas de centenas de KB (tool_result grande); o default
# do StreamReader (64 KB) estouraria com LimitOverrunError.
STDOUT_LINE_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class Init:
    session_id: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStart:
    name: str
    detail: str


@dataclass(frozen=True)
class ToolDone:
    is_error: bool


@dataclass(frozen=True)
class Result:
    text: str
    is_error: bool
    session_id: str | None
    cost_usd: float | None
    num_turns: int | None
    permission_denials: int


@dataclass(frozen=True)
class Exited:
    return_code: int | None
    stderr: str


Event = Init | TextDelta | ToolStart | ToolDone | Result | Exited


@dataclass(frozen=True)
class RunSpec:
    prompt: str
    cwd: str
    session_id: str | None
    yolo: bool
    permission_mode: str
    allowed_tools: str
    add_dirs: tuple[str, ...]
    append_system_prompt: str


def _tool_detail(name: str, inp: dict) -> str:
    if name == "Bash":
        value = inp.get("command", "")
    elif name in ("Read", "Edit", "Write", "NotebookEdit"):
        value = inp.get("file_path", "")
    elif name in ("Grep", "Glob"):
        value = inp.get("pattern", "")
    elif name == "Agent":
        value = inp.get("description", "")
    else:
        value = json.dumps(inp, ensure_ascii=False) if inp else ""
    value = " ".join(str(value).split())
    return value[:80] + ("…" if len(value) > 80 else "")


def parse_line(line: str) -> list[Event]:
    """Converte uma linha do stream-json em zero ou mais eventos. Puro, sem I/O."""
    line = line.strip()
    if not line:
        return []
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        log.warning("linha não-JSON no stream: %.120s", line)
        return []

    kind = ev.get("type")
    if kind == "system" and ev.get("subtype") == "init":
        return [Init(ev["session_id"])]

    if kind == "stream_event":
        delta = ev.get("event", {}).get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            return [TextDelta(delta["text"])]
        return []

    if kind == "assistant":
        content = ev.get("message", {}).get("content") or []
        return [
            ToolStart(
                block.get("name", "?"),
                _tool_detail(block.get("name", ""), block.get("input") or {}),
            )
            for block in content
            if block.get("type") == "tool_use"
        ]

    if kind == "user":
        content = ev.get("message", {}).get("content") or []
        if isinstance(content, list):
            return [
                ToolDone(bool(block.get("is_error")))
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
        return []

    if kind == "result":
        return [
            Result(
                text=ev.get("result") or "",
                is_error=bool(ev.get("is_error")) or ev.get("subtype") != "success",
                session_id=ev.get("session_id"),
                cost_usd=ev.get("total_cost_usd"),
                num_turns=ev.get("num_turns"),
                permission_denials=len(ev.get("permission_denials") or []),
            )
        ]

    return []


def build_args(claude_bin: str, spec: RunSpec) -> list[str]:
    args = [
        claude_bin,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--append-system-prompt",
        spec.append_system_prompt,
    ]
    if spec.session_id:
        args += ["--resume", spec.session_id]
    if spec.yolo:
        args.append("--dangerously-skip-permissions")
    else:
        if spec.permission_mode:
            args += ["--permission-mode", spec.permission_mode]
        if spec.allowed_tools:
            args += ["--allowedTools", spec.allowed_tools]
    for d in spec.add_dirs:
        args += ["--add-dir", d]
    # Prompt sempre posicional depois de `--`: texto começando com "-" quebraria o parser.
    args += ["--", spec.prompt]
    return args


class ClaudeProcess:
    """Um processo do Claude Code. Itere `events()`; `kill()` aborta a árvore toda."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._stderr_task = asyncio.create_task(proc.stderr.read())  # type: ignore[union-attr]

    @property
    def pid(self) -> int:
        return self._proc.pid

    def kill(self) -> None:
        try:
            os.killpg(self._proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        asyncio.get_running_loop().call_later(3, self._force_kill)

    def _force_kill(self) -> None:
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._proc.pid, signal.SIGKILL)

    async def events(self) -> AsyncIterator[Event]:
        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            for ev in parse_line(raw.decode("utf-8", errors="replace")):
                yield ev
        stderr = (await self._stderr_task).decode("utf-8", errors="replace")
        rc = await self._proc.wait()
        yield Exited(rc, stderr)


class SubprocessRunner:
    def __init__(self, claude_bin: str) -> None:
        self._bin = claude_bin

    async def start(self, spec: RunSpec) -> ClaudeProcess:
        args = build_args(self._bin, spec)
        log.info("claude start cwd=%s resume=%s yolo=%s", spec.cwd, spec.session_id, spec.yolo)
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=spec.cwd,
            stdin=asyncio.subprocess.DEVNULL,  # sem isso o CLI espera 3s por stdin
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STDOUT_LINE_LIMIT,
            env={**os.environ, "CLAUDE_BOT_SKIP_HOOKS": "1"},
            start_new_session=True,
        )
        return ClaudeProcess(proc)
