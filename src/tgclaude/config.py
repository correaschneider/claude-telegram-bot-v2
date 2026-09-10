"""Configuração centralizada: lê o ambiente uma vez e devolve um objeto imutável."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "Você está conversando pelo Telegram, provavelmente com o usuário no celular. "
    "Seja direto e conciso; prefira listas curtas a tabelas largas; evite blocos enormes de saída.\n"
    "Tools do Telegram (servidor MCP `tg`):\n"
    "- mcp__tg__update_checklist: se a tarefa tiver 3+ etapas, registre o plano ao começar e "
    "reenvie a lista inteira (done=true nas concluídas) a cada etapa.\n"
    "- mcp__tg__ask_user: quando precisar de uma decisão do usuário entre alternativas claras, "
    "pergunte por aqui (botões) em vez de em texto; espere a resposta antes de seguir.\n"
    "- mcp__tg__send_file: entrega arquivos ao usuário. Imagens/PDF/áudio/vídeo citados por "
    "caminho absoluto na resposta já são enviados automaticamente; use a tool para .md/.json/.txt.\n"
    "- mcp__tg__schedule / mcp__tg__unschedule: tarefas recorrentes pedidas pelo usuário "
    "(cron de 5 campos para horários de calendário; every_seconds>=5 para intervalos curtos)."
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
    jobs_file: str
    permissions_file: str  # listas read-only/sensível/caminhos (opcional; defaults embutidos)
    decisions_file: str  # log de decisões de permissão (/audit)
    default_tz: str
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

    whisperx_server_url: str  # servidor residente; vazio = só CLI
    whisperx_bin: str
    whisperx_model: str
    whisperx_device: str
    whisperx_language: str
    whisperx_compute_type: str
    whisperx_batch: str
    whisperx_lock: str
    audio_tmp_dir: str
    image_tmp_dir: str
    video_tmp_dir: str  # vídeo + transcrição + prints ficam aqui
    video_max_moments: int  # teto de momentos-chave (prints) por vídeo

    @classmethod
    def from_env(cls) -> Config:
        cwd = os.getcwd()
        env = os.environ
        chats = frozenset(int(x) for x in _split_csv(env["ALLOWED_CHAT_IDS"]))
        users_raw = _split_csv(env.get("ALLOWED_USER_IDS", ""))
        users = (
            frozenset(int(x) for x in users_raw)
            if users_raw
            else frozenset(c for c in chats if c > 0)
        )
        return cls(
            telegram_token=env["TELEGRAM_TOKEN"],
            allowed_chat_ids=chats,
            allowed_user_ids=users,
            workspace=env["WORKSPACE"],
            projects_file=env.get("PROJECTS_FILE") or os.path.join(cwd, "projects.json"),
            claude_bin=env.get("CLAUDE_BIN", "claude"),
            claude_permission_mode=env.get("CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_allowed_tools=env.get("CLAUDE_ALLOWED_TOOLS", "").strip(),
            claude_add_dirs=_split_csv(env.get("CLAUDE_ADD_DIRS", "")),
            claude_append_system_prompt=env.get("CLAUDE_APPEND_SYSTEM_PROMPT", "").strip()
            or DEFAULT_SYSTEM_PROMPT,
            store_file=env.get("STORE_FILE") or os.path.join(cwd, ".store.json"),
            jobs_file=env.get("JOBS_FILE") or os.path.join(cwd, ".jobs.json"),
            permissions_file=env.get("PERMISSIONS_FILE") or os.path.join(cwd, "permissions.json"),
            decisions_file=env.get("DECISIONS_FILE") or os.path.join(cwd, ".decisions.jsonl"),
            default_tz=env.get("DEFAULT_TZ", "America/Sao_Paulo"),
            session_ttl=int(env.get("SESSION_TTL_SECONDS", str(6 * 3600))),
            yolo_ttl=int(env.get("YOLO_TTL_SECONDS", "3600")),
            draft_interval=float(env.get("DRAFT_INTERVAL_SECONDS", "1.5")),
            draft_keepalive=float(env.get("DRAFT_KEEPALIVE_SECONDS", "10")),
            draft_max_chars=int(env.get("DRAFT_MAX_CHARS", "3500")),
            rich_messages=_bool(env.get("RICH_MESSAGES"), True),
            footer_cost=_bool(env.get("FOOTER_COST"), False),
            bridge_host=env.get("BRIDGE_HOST", "127.0.0.1"),
            bridge_port=int(env.get("BRIDGE_PORT", "0")),
            mcp_tool_timeout_ms=int(env.get("MCP_TOOL_TIMEOUT_MS", str(24 * 3600 * 1000))),
            group_public_tag=env.get("GROUP_PUBLIC_TAG", "#todos"),
            whisperx_server_url=env.get("WHISPERX_SERVER_URL", "http://127.0.0.1:8765").strip(),
            whisperx_bin=env.get("WHISPERX_BIN") or os.path.expanduser("~/.local/bin/whisperx-cli"),
            whisperx_model=env.get("WHISPERX_MODEL", "large-v3"),
            whisperx_device=env.get("WHISPERX_DEVICE", "cuda"),
            whisperx_language=env.get("WHISPERX_LANGUAGE", "pt"),
            whisperx_compute_type=env.get("WHISPERX_COMPUTE_TYPE", "float16"),
            whisperx_batch=env.get("WHISPERX_BATCH", "8"),
            whisperx_lock=env.get("WHISPERX_LOCK", "/tmp/whisperx-pipeline.lock"),
            audio_tmp_dir=env.get("AUDIO_TMP_DIR", "/tmp/telegram-audio"),
            image_tmp_dir=env.get("IMAGE_TMP_DIR", "/tmp/telegram-images"),
            video_tmp_dir=env.get("VIDEO_TMP_DIR", "/tmp/telegram-videos"),
            video_max_moments=int(env.get("VIDEO_MAX_MOMENTS", "8")),
        )
