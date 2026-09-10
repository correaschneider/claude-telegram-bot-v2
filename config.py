"""Configuração centralizada: lê o ambiente uma vez e devolve um objeto imutável."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "Você está conversando pelo Telegram, provavelmente com o usuário no celular. "
    "Seja direto e conciso; prefira listas curtas a tabelas largas; evite blocos enormes de saída. "
    "Se a tarefa tiver 3 ou mais etapas, chame a tool mcp__tg__update_checklist ao planejar "
    "(todas as etapas) e sempre que concluir uma etapa (reenvie a lista inteira marcando done=true)."
)


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_chat_ids: frozenset[int]  # chats privados e grupos que o bot atende
    allowed_user_ids: frozenset[int]  # quem pode disparar turnos em grupo/guest e aprovar

    workspace: str
    projects_file: str
    claude_bin: str
    claude_permission_mode: str
    claude_allowed_tools: str
    claude_add_dirs: tuple[str, ...]
    claude_append_system_prompt: str

    store_file: str
    session_ttl: int
    yolo_ttl: int

    draft_interval: float
    draft_keepalive: float
    draft_max_chars: int
    rich_messages: bool
    footer_cost: bool  # além dos tokens, mostrar custo em USD no rodapé

    bridge_host: str
    bridge_port: int
    mcp_tool_timeout_ms: int

    group_public_tag: str

    @classmethod
    def from_env(cls) -> Config:
        cwd = os.getcwd()
        chats = frozenset(int(x) for x in _split_csv(os.environ["ALLOWED_CHAT_IDS"]))
        users_raw = _split_csv(os.environ.get("ALLOWED_USER_IDS", ""))
        users = (
            frozenset(int(x) for x in users_raw)
            if users_raw
            else frozenset(c for c in chats if c > 0)
        )
        return cls(
            telegram_token=os.environ["TELEGRAM_TOKEN"],
            allowed_chat_ids=chats,
            allowed_user_ids=users,
            workspace=os.environ["WORKSPACE"],
            projects_file=os.environ.get("PROJECTS_FILE") or os.path.join(cwd, "projects.json"),
            claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
            claude_permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_allowed_tools=os.environ.get("CLAUDE_ALLOWED_TOOLS", "").strip(),
            claude_add_dirs=_split_csv(os.environ.get("CLAUDE_ADD_DIRS", "")),
            claude_append_system_prompt=os.environ.get("CLAUDE_APPEND_SYSTEM_PROMPT", "").strip()
            or DEFAULT_SYSTEM_PROMPT,
            store_file=os.environ.get("STORE_FILE") or os.path.join(cwd, ".store.json"),
            session_ttl=int(os.environ.get("SESSION_TTL_SECONDS", str(6 * 3600))),
            yolo_ttl=int(os.environ.get("YOLO_TTL_SECONDS", "3600")),
            draft_interval=float(os.environ.get("DRAFT_INTERVAL_SECONDS", "1.5")),
            draft_keepalive=float(os.environ.get("DRAFT_KEEPALIVE_SECONDS", "10")),
            draft_max_chars=int(os.environ.get("DRAFT_MAX_CHARS", "3500")),
            rich_messages=_bool(os.environ.get("RICH_MESSAGES"), True),
            footer_cost=_bool(os.environ.get("FOOTER_COST"), False),
            bridge_host=os.environ.get("BRIDGE_HOST", "127.0.0.1"),
            bridge_port=int(os.environ.get("BRIDGE_PORT", "0")),
            mcp_tool_timeout_ms=int(os.environ.get("MCP_TOOL_TIMEOUT_MS", str(24 * 3600 * 1000))),
            group_public_tag=os.environ.get("GROUP_PUBLIC_TAG", "#todos"),
        )
