"""Configuração centralizada: lê o ambiente uma vez e devolve um objeto imutável."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "Você está conversando pelo Telegram, provavelmente com o usuário no celular. "
    "Seja direto e conciso; prefira listas curtas a tabelas largas; evite blocos enormes de saída."
)


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_chat_ids: frozenset[int]

    workspace: str
    claude_bin: str
    claude_permission_mode: str
    claude_allowed_tools: str
    claude_add_dirs: tuple[str, ...]
    claude_append_system_prompt: str

    sessions_file: str
    session_ttl: int

    draft_interval: float
    draft_keepalive: float
    draft_max_chars: int

    @classmethod
    def from_env(cls) -> Config:
        cwd = os.getcwd()
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_chat_ids=frozenset(int(x) for x in _split_csv(os.environ["ALLOWED_CHAT_IDS"])),
            workspace=os.environ["WORKSPACE"],
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            claude_permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_allowed_tools=os.environ.get("CLAUDE_ALLOWED_TOOLS", "").strip(),
            claude_add_dirs=_split_csv(os.environ.get("CLAUDE_ADD_DIRS", "")),
            claude_append_system_prompt=os.environ.get("CLAUDE_APPEND_SYSTEM_PROMPT", "").strip()
            or DEFAULT_SYSTEM_PROMPT,
            sessions_file=os.environ.get("SESSIONS_FILE") or os.path.join(cwd, ".sessions.json"),
            session_ttl=int(os.environ.get("SESSION_TTL_SECONDS", str(6 * 3600))),
            draft_interval=float(os.environ.get("DRAFT_INTERVAL_SECONDS", "1.5")),
            draft_keepalive=float(os.environ.get("DRAFT_KEEPALIVE_SECONDS", "10")),
            draft_max_chars=int(os.environ.get("DRAFT_MAX_CHARS", "3500")),
        )
