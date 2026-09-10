"""Handlers finos do aiogram: traduzem updates em chamadas ao núcleo (turn.py)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, MessageGenerationStopped, Update

from claude_stream import RunSpec
from formatting import format_elapsed
from services import ActiveTurn, Services
from turn import Turn

log = logging.getLogger("claude-bot")
router = Router()


class AllowlistMiddleware(BaseMiddleware):
    """Derruba qualquer update (mensagem, stop, callback…) de chat fora da allowlist."""

    def __init__(self, allowed: frozenset[int]) -> None:
        self._allowed = allowed

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _chat_id_of(event)
        if chat_id not in self._allowed:
            log.warning("update ignorado de chat não autorizado: %s", chat_id)
            return None
        return await handler(event, data)


def _chat_id_of(update: Update) -> int | None:
    if update.message:
        return update.message.chat.id
    if update.stopped_message_generation:
        return update.stopped_message_generation.chat.id
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message.chat.id
    if update.edited_message:
        return update.edited_message.chat.id
    return None


@router.message(CommandStart())
async def on_start(message: Message, services: Services) -> None:
    await message.answer(
        "Bot v2: streaming do Claude Code via rascunho vivo.\n"
        f"Workspace: {services.cfg.workspace}\n\n"
        "Mande texto. Comandos: /status /reset /cancel /yolo"
    )


@router.message(Command("status"))
async def on_status(message: Message, services: Services) -> None:
    chat_id = message.chat.id
    sid = services.sessions.get(chat_id)
    active = services.state.active.get(chat_id)
    running = (
        f"⚙️ processando há {format_elapsed(time.monotonic() - active.started_at)}"
        if active
        else "💤 ocioso"
    )
    lines = [
        f"📁 {services.cfg.workspace}",
        f"🧵 sessão: {sid[:8] if sid else '— (nova no próximo turno)'}",
        f"🔓 yolo: {'ON' if chat_id in services.state.yolo else 'off'}",
        running,
    ]
    await message.answer("\n".join(lines))


@router.message(Command("reset"))
async def on_reset(message: Message, services: Services) -> None:
    services.sessions.clear(message.chat.id)
    await message.answer("🧹 Sessão zerada. O próximo turno começa do zero.")


@router.message(Command("cancel"))
async def on_cancel(message: Message, services: Services) -> None:
    active = services.state.active.get(message.chat.id)
    if not active:
        await message.answer("Nada rodando.")
        return
    active.turn.stop()
    await message.answer("⏹ Cancelando…")


@router.message(Command("yolo"))
async def on_yolo(message: Message, services: Services) -> None:
    chat_id = message.chat.id
    if chat_id in services.state.yolo:
        services.state.yolo.discard(chat_id)
        await message.answer("🔒 yolo OFF — permissões via CLAUDE_PERMISSION_MODE/ALLOWED_TOOLS.")
    else:
        services.state.yolo.add(chat_id)
        await message.answer("🔓 yolo ON — --dangerously-skip-permissions neste chat.")


@router.stopped_message_generation()
async def on_stop_button(event: MessageGenerationStopped, services: Services) -> None:
    active = services.state.active.get(event.chat.id)
    if active and active.turn.draft_id == event.draft_id:
        log.info("stop pelo botão: chat=%s draft=%s", event.chat.id, event.draft_id)
        active.turn.stop()


@router.message(F.text)
async def on_text(message: Message, services: Services) -> None:
    chat_id = message.chat.id
    if chat_id in services.state.active:
        await message.answer("⏳ Ainda processando o turno anterior. /cancel pra interromper.")
        return

    cfg = services.cfg
    spec = RunSpec(
        prompt=message.text or "",
        cwd=cfg.workspace,
        session_id=services.sessions.get(chat_id),
        yolo=chat_id in services.state.yolo,
        permission_mode=cfg.claude_permission_mode,
        allowed_tools=cfg.claude_allowed_tools,
        add_dirs=cfg.claude_add_dirs,
        append_system_prompt=cfg.claude_append_system_prompt,
    )
    turn = Turn(
        services.sink,
        chat_id,
        draft_id=message.message_id,
        interval=cfg.draft_interval,
        keepalive=cfg.draft_keepalive,
        max_chars=cfg.draft_max_chars,
    )
    services.state.active[chat_id] = ActiveTurn(turn)
    try:
        proc = await services.runner.start(spec)
        outcome = await turn.run(proc)
    except Exception:
        log.exception("turno falhou chat=%s", chat_id)
        await message.answer("❌ Falha interna ao rodar o turno; veja o log do bot.")
        return
    finally:
        services.state.active.pop(chat_id, None)

    if outcome.session_id:
        services.sessions.set(chat_id, outcome.session_id)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Por enquanto só texto (MVP). Voz/imagem vêm depois.")
