"""Núcleo de um turno: consome os eventos do Claude e mantém o rascunho vivo no
Telegram (sendMessageDraft), depois entrega a resposta definitiva. Só fala com portas."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from claude_stream import Event, Exited, Init, Result, TextDelta, ToolDone, ToolStart
from formatting import fmt_tokens, format_elapsed

log = logging.getLogger("claude-bot")


class RunningClaude(Protocol):
    def events(self) -> AsyncIterator[Event]: ...
    def kill(self) -> None: ...


@dataclass
class TurnOutcome:
    text: str  # resposta principal (último trecho de texto)
    progress: str  # trechos anteriores (entre ferramentas) — vira bloco colapsável
    steps: int  # ferramentas executadas
    footer: str
    session_id: str | None
    stopped: bool
    error: bool
    elapsed: float


class DraftSink(Protocol):
    """Porta do Telegram: rascunho vivo e entrega final."""

    async def draft(
        self, chat_id: int, thread_id: int | None, draft_id: int, text: str
    ) -> None: ...
    async def send(self, chat_id: int, thread_id: int | None, outcome: TurnOutcome) -> None: ...


@dataclass
class _Buffer:
    segments: list[list[str]] = field(default_factory=lambda: [[]])
    steps: int = 0

    def append(self, text: str) -> None:
        self.segments[-1].append(text)

    def new_segment(self) -> None:
        if self.segments[-1]:
            self.segments.append([])

    @property
    def texts(self) -> list[str]:
        return [t for seg in self.segments if (t := "".join(seg).strip())]

    @property
    def full(self) -> str:
        return "\n\n".join(self.texts)


class Turn:
    def __init__(
        self,
        sink: DraftSink,
        chat_id: int,
        thread_id: int | None,
        draft_id: int,
        *,
        interval: float,
        keepalive: float,
        max_chars: int,
        show_cost: bool = False,
    ) -> None:
        self._sink = sink
        self._chat = chat_id
        self._thread = thread_id
        self._draft_id = draft_id
        self._interval = interval
        self._keepalive = keepalive
        self._max_chars = max_chars
        self._show_cost = show_cost

        self._buf = _Buffer()
        self._status = ""
        self._dirty = False
        self._last_sent = 0.0
        self._stopped = False
        self._proc: RunningClaude | None = None
        self._flush_lock = asyncio.Lock()

    @property
    def text(self) -> str:
        return self._buf.full

    @property
    def draft_id(self) -> int:
        return self._draft_id

    def stop(self) -> None:
        self._stopped = True
        if self._proc is not None:
            self._proc.kill()

    def set_status(self, status: str) -> None:
        """Linha de estado extra (ex.: aguardando aprovação). Vazio limpa."""
        self._status = status
        self._dirty = True

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
            await self._sink.draft(self._chat, self._thread, self._draft_id, self.render())

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self._dirty or time.monotonic() - self._last_sent >= self._keepalive:
                await self._flush()

    # ---- eventos ----

    def _apply(self, ev: Event) -> None:
        if isinstance(ev, TextDelta):
            self._buf.append(ev.text)
        elif isinstance(ev, ToolStart):
            self._status = f"🔧 {ev.name} · {ev.detail}" if ev.detail else f"🔧 {ev.name}"
        elif isinstance(ev, ToolDone):
            self._status = "⚠️ ferramenta falhou, seguindo…" if ev.is_error else ""
            self._buf.steps += 1
            self._buf.new_segment()
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
        await self._sink.send(self._chat, self._thread, outcome)
        return outcome

    def _outcome(
        self, result: Result | None, exited: Exited | None, session_id: str | None, elapsed: float
    ) -> TurnOutcome:
        texts = self._buf.texts
        steps = self._buf.steps

        def out(text: str, *, progress: str = "", footer: str = "", stopped=False, error=False):
            return TurnOutcome(text, progress, steps, footer, session_id, stopped, error, elapsed)

        if self._stopped:
            partial = "\n\n".join(texts)
            return out("⏹ Interrompido." + (f"\n\n{partial}" if partial else ""), stopped=True)

        if result is None:
            rc = exited.return_code if exited else None
            tail = (exited.stderr.strip()[-600:] if exited else "") or "(sem stderr)"
            return out(f"❌ Claude encerrou sem resultado (rc={rc}).\n```\n{tail}\n```", error=True)

        if result.is_error:
            return out(f"❌ {result.text or 'erro sem detalhe'}", error=True)

        text = (texts[-1] if texts else "") or result.text.strip() or "(resposta vazia)"
        progress = "\n\n".join(texts[:-1]) if len(texts) > 1 else ""
        footer = f"⏱ {format_elapsed(elapsed)}"
        if result.input_tokens or result.output_tokens:
            footer += (
                f" · ↓{fmt_tokens(result.input_tokens)} ↑{fmt_tokens(result.output_tokens)} tokens"
            )
        if self._show_cost and result.cost_usd is not None:
            footer += f" · {result.cost_usd:.2f} USD"
        if result.num_turns:
            footer += f" · {result.num_turns} turnos"
        if result.permission_denials:
            footer += (
                f"\n🔒 {result.permission_denials} chamada(s) negada(s) por permissão"
                " — aprove pelo botão, use /yolo ou ajuste allowed_tools"
            )
        return out(text, progress=progress, footer=footer)
