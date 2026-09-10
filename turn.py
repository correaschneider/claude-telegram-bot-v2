"""Núcleo de um turno: consome os eventos do Claude e mantém o rascunho vivo no
Telegram (sendMessageDraft), depois envia a resposta definitiva. Só fala com portas."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from claude_stream import Event, Exited, Init, Result, TextDelta, ToolDone, ToolStart
from formatting import format_elapsed

log = logging.getLogger("claude-bot")


class RunningClaude(Protocol):
    def events(self) -> AsyncIterator[Event]: ...
    def kill(self) -> None: ...


class DraftSink(Protocol):
    """Porta do Telegram: rascunho vivo e mensagem definitiva."""

    async def draft(self, chat_id: int, draft_id: int, text: str) -> None: ...
    async def send(self, chat_id: int, markdown: str) -> None: ...


@dataclass
class TurnOutcome:
    text: str
    session_id: str | None
    stopped: bool
    error: bool
    elapsed: float


class Turn:
    def __init__(
        self,
        sink: DraftSink,
        chat_id: int,
        draft_id: int,
        *,
        interval: float,
        keepalive: float,
        max_chars: int,
    ) -> None:
        self._sink = sink
        self._chat = chat_id
        self._draft_id = draft_id
        self._interval = interval
        self._keepalive = keepalive
        self._max_chars = max_chars

        self._buf: list[str] = []
        self._status = ""
        self._need_sep = False
        self._dirty = False
        self._last_sent = 0.0
        self._stopped = False
        self._proc: RunningClaude | None = None
        self._flush_lock = asyncio.Lock()

    @property
    def text(self) -> str:
        return "".join(self._buf)

    @property
    def draft_id(self) -> int:
        return self._draft_id

    def stop(self) -> None:
        self._stopped = True
        if self._proc is not None:
            self._proc.kill()

    # ---- rascunho ----

    def render(self) -> str:
        body = self.text
        if len(body) > self._max_chars:
            body = "…" + body[-self._max_chars :]
        if self._status:
            body = f"{body}\n\n{self._status}" if body else self._status
        return body

    async def _flush(self) -> None:
        async with self._flush_lock:
            self._dirty = False
            self._last_sent = time.monotonic()
            await self._sink.draft(self._chat, self._draft_id, self.render())

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self._dirty or time.monotonic() - self._last_sent >= self._keepalive:
                await self._flush()

    # ---- eventos ----

    def _apply(self, ev: Event) -> None:
        if isinstance(ev, TextDelta):
            if self._need_sep and self._buf and not self.text.endswith("\n"):
                self._buf.append("\n\n")
            self._need_sep = False
            self._buf.append(ev.text)
        elif isinstance(ev, ToolStart):
            self._status = f"🔧 {ev.name} · {ev.detail}" if ev.detail else f"🔧 {ev.name}"
        elif isinstance(ev, ToolDone):
            self._status = "⚠️ ferramenta falhou, seguindo…" if ev.is_error else ""
            self._need_sep = True
        else:
            return
        self._dirty = True

    async def run(self, proc: RunningClaude) -> TurnOutcome:
        self._proc = proc
        started = time.monotonic()
        session_id: str | None = None
        result: Result | None = None
        exited: Exited | None = None

        await self._flush()  # texto vazio → placeholder "Thinking…" nativo
        ticker = asyncio.create_task(self._tick())
        try:
            async for ev in proc.events():
                if isinstance(ev, Init):
                    session_id = ev.session_id
                elif isinstance(ev, Result):
                    result = ev
                    session_id = session_id or ev.session_id
                elif isinstance(ev, Exited):
                    exited = ev
                else:
                    self._apply(ev)
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            async with self._flush_lock:  # espera um flush em voo antes da mensagem final
                pass

        elapsed = time.monotonic() - started
        outcome = self._outcome(result, exited, session_id, elapsed)
        await self._sink.send(self._chat, outcome.text)
        return outcome

    def _outcome(
        self, result: Result | None, exited: Exited | None, session_id: str | None, elapsed: float
    ) -> TurnOutcome:
        text = self.text.strip()
        if self._stopped:
            body = "⏹ Interrompido." + (f"\n\n{text}" if text else "")
            return TurnOutcome(body, session_id, True, False, elapsed)

        if result is None:
            rc = exited.return_code if exited else None
            tail = (exited.stderr.strip()[-600:] if exited else "") or "(sem stderr)"
            body = f"❌ Claude encerrou sem resultado (rc={rc}).\n```\n{tail}\n```"
            return TurnOutcome(body, session_id, False, True, elapsed)

        if result.is_error:
            body = f"❌ {result.text or 'erro sem detalhe'}"
            return TurnOutcome(body, session_id, False, True, elapsed)

        body = text or result.text.strip() or "(resposta vazia)"
        footer = f"⏱ {format_elapsed(elapsed)}"
        if result.cost_usd is not None:
            footer += f" · ${result.cost_usd:.2f}"
        if result.num_turns:
            footer += f" · {result.num_turns} turnos"
        if result.permission_denials:
            footer += f"\n🔒 {result.permission_denials} chamada(s) de ferramenta negada(s) — /yolo ou ajuste CLAUDE_ALLOWED_TOOLS"
        return TurnOutcome(f"{body}\n\n{footer}", session_id, False, False, elapsed)
