"""Orquestra um turno: monta o RunSpec a partir da conversa/projeto, registra o turno pra
bridge, roda o núcleo e persiste sessão/título. Handlers só chamam `run_turn`."""

from __future__ import annotations

import contextlib
import json
import logging
import secrets

from aiogram.exceptions import TelegramBadRequest

from claude_stream import RunSpec
from delivery import send_generated_files
from permissions import READ_ONLY_RULES, split_rules
from services import ActiveTurn, Services
from store import Conversation
from turn import DraftSink, Turn, TurnOutcome

log = logging.getLogger("claude-bot")

TG_TOOLS = "mcp__tg__*"  # checklist, ask_user, send_file, schedule… liberadas sem prompt
# Regra `ask` é avaliada ANTES das `allow` (inclusive as globais do ~/.claude/settings.json),
# então todo Bash cai no prompt tool e a política fica 100% na mesa do bot.
ASK_SETTINGS = json.dumps({"permissions": {"ask": ["Bash"]}})


def is_busy(services: Services, conv: Conversation) -> bool:
    return conv.key in services.state.active


async def run_turn(
    services: Services,
    conv: Conversation,
    prompt: str,
    *,
    draft_id: int,
    sink: DraftSink,
    actor_id: int,
    allow_prompt: bool,
) -> TurnOutcome:
    cfg = services.cfg
    project = services.projects.get_or_default(conv.project)
    token = secrets.token_hex(8)
    yolo = allow_prompt and conv.yolo()

    mcp_config = json.dumps(
        {
            "mcpServers": {
                "tg": {
                    "type": "stdio",
                    "command": services.bridge.python,
                    "args": [services.bridge.script],
                    "env": {
                        "TG_BRIDGE_URL": services.bridge.url,
                        "TG_BRIDGE_TOKEN": services.bridge.token,
                        "TG_TURN": token,
                    },
                }
            }
        }
    )
    project_rules = split_rules(project.allowed_tools or cfg.claude_allowed_tools)
    allowed = " ".join([*project_rules, *conv.allow, TG_TOOLS])
    spec = RunSpec(
        prompt=prompt,
        cwd=project.path,
        session_id=conv.fork_from or conv.session_id,
        fork_session=bool(conv.fork_from),
        yolo=yolo,
        permission_mode=project.permission_mode or cfg.claude_permission_mode,
        allowed_tools=allowed,
        add_dirs=project.add_dirs or cfg.claude_add_dirs,
        append_system_prompt=cfg.claude_append_system_prompt,
        mcp_config=mcp_config,
        permission_prompt_tool=None if yolo else "mcp__tg__ask",
        settings_json=None if yolo else ASK_SETTINGS,
        env={"MCP_TOOL_TIMEOUT": str(cfg.mcp_tool_timeout_ms)},
    )
    turn = Turn(
        sink,
        conv.chat_id,
        conv.topic_id or None,
        draft_id,
        interval=cfg.draft_interval,
        keepalive=cfg.draft_keepalive,
        max_chars=cfg.draft_max_chars,
        show_cost=cfg.footer_cost,
    )
    active = ActiveTurn(
        conv=conv,
        turn=turn,
        token=token,
        actor_id=actor_id,
        rules=[*READ_ONLY_RULES, *project_rules],
        can_prompt=allow_prompt,
    )
    services.state.active[conv.key] = active
    services.state.by_token[token] = active
    try:
        proc = await services.runner.start(spec)
        outcome = await turn.run(proc)
    finally:
        services.state.active.pop(conv.key, None)
        services.state.by_token.pop(token, None)
        services.desk.cancel_all(active)
        services.questions.cancel_all(active)

    if allow_prompt and not outcome.error:
        with contextlib.suppress(Exception):
            await send_generated_files(
                services.bot,
                conv.chat_id,
                conv.topic_id or None,
                f"{outcome.progress}\n{outcome.text}",
                sent=active.sent_files,
            )
    if outcome.session_id and not conv.job_id:
        services.store.set_session(conv, outcome.session_id)
    if conv.auto_title and conv.topic_id and not outcome.error:
        conv.auto_title = False
        services.store.put(conv)
        title = f"{project.alias} · {' '.join(prompt.split())[:40]}"
        with contextlib.suppress(TelegramBadRequest):
            await services.bot.edit_forum_topic(conv.chat_id, conv.topic_id, name=title[:128])
    return outcome
