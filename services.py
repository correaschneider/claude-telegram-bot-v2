"""Container de dependências injetado nos handlers + estado efêmero do processo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from claude_stream import RunSpec
from config import Config
from sessions import SessionStore
from turn import DraftSink, RunningClaude, Turn


class ClaudeRunner(Protocol):
    async def start(self, spec: RunSpec) -> RunningClaude: ...


@dataclass
class ActiveTurn:
    turn: Turn
    started_at: float = field(default_factory=time.monotonic)


@dataclass
class RuntimeState:
    active: dict[int, ActiveTurn] = field(default_factory=dict)
    yolo: set[int] = field(default_factory=set)


@dataclass
class Services:
    cfg: Config
    runner: ClaudeRunner
    sink: DraftSink
    sessions: SessionStore
    state: RuntimeState
