"""Fakes e constantes compartilhados pelos testes (Claude e Telegram simulados)."""

from __future__ import annotations

import asyncio
import importlib.util

from tgclaude.claude.stream import (
    Exited,
)
from tgclaude.core.turn import Turn, TurnOutcome

CHAT = 123
THREAD = 9
DRAFT = 77
BRIDGE = importlib.util.find_spec("tgclaude.claude.mcp_bridge").origin


class FakeSink:
    def __init__(self) -> None:
        self.drafts: list[str] = []
        self.sent: list[TurnOutcome] = []

    async def draft(self, chat_id, thread_id, draft_id, text):
        assert (chat_id, thread_id, draft_id) == (CHAT, THREAD, DRAFT)
        self.drafts.append(text)

    async def send(self, chat_id, thread_id, outcome):
        assert (chat_id, thread_id) == (CHAT, THREAD)
        self.sent.append(outcome)


class FakeProc:
    """Emite eventos roteirizados; `None` no roteiro = pausa (simula Claude pensando)."""

    def __init__(self, script, pause=0.03):
        self._script = script
        self._pause = pause
        self.killed = False

    def kill(self):
        self.killed = True

    async def events(self):
        for item in self._script:
            if self.killed:
                break
            if item is None:
                await asyncio.sleep(self._pause)
            else:
                yield item
        yield Exited(0 if not self.killed else -15, "")


def make_turn(sink, interval=0.01, keepalive=0.05):
    return Turn(sink, CHAT, THREAD, DRAFT, interval=interval, keepalive=keepalive, max_chars=3500)
