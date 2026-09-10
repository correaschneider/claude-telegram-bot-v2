"""Container de dependências injetado nos handlers + estado efêmero do processo."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from claude_stream import RunSpec
from config import Config
from permissions import PolicyLoader
from projects import ProjectRegistry
from store import Conversation, ConversationStore
from turn import RunningClaude, Turn

if TYPE_CHECKING:
    from aiogram import Bot

    from permission_desk import PermissionDesk
    from questions import QuestionDesk
    from scheduler import JobScheduler


class ClaudeRunner(Protocol):
    async def start(self, spec: RunSpec) -> RunningClaude: ...


class Transcriber(Protocol):
    async def transcribe(self, audio_path: str) -> str: ...


@dataclass
class ActiveTurn:
    conv: Conversation
    turn: Turn
    token: str  # identifica o turno pra bridge MCP
    actor_id: int  # quem disparou (aprova permissões / recebe efêmera)
    rules: list[str] = field(default_factory=list)  # auto-aprova (read-only + projeto)
    can_prompt: bool = True  # False em grupo/guest: sem humano pra aprovar → nega
    session_allow: list[str] = field(default_factory=list)  # "sempre" aprovado neste turno
    sent_files: set[str] = field(default_factory=set)  # dedup entre send_file e auto-detecção
    started_at: float = field(default_factory=time.monotonic)


@dataclass
class RuntimeState:
    active: dict[str, ActiveTurn] = field(default_factory=dict)  # conv.key → turno
    by_token: dict[str, ActiveTurn] = field(default_factory=dict)
    session_picks: dict[int, list[dict]] = field(default_factory=dict)  # /sessions por chat
    audit_picks: dict[int, list[tuple[str, str]]] = field(
        default_factory=dict
    )  # /audit: (alias, regra)


@dataclass
class Bridge:
    url: str
    token: str
    script: str  # caminho absoluto do mcp_bridge.py
    python: str


@dataclass
class Services:
    cfg: Config
    bot: Bot
    bot_username: str
    bot_id: int
    projects: ProjectRegistry
    policy: PolicyLoader
    store: ConversationStore
    runner: ClaudeRunner
    state: RuntimeState
    bridge: Bridge
    desk: PermissionDesk
    questions: QuestionDesk
    scheduler: JobScheduler
    transcriber: Transcriber
