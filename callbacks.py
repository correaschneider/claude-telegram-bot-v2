"""Handlers comuns a qualquer chat: botões inline e o Stop nativo do rascunho."""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, MessageGenerationStopped

from services import Services

log = logging.getLogger("claude-bot")
router = Router(name="common")


@router.callback_query(F.data.startswith("perm:"))
async def on_perm(cq: CallbackQuery, services: Services) -> None:
    if cq.from_user.id not in services.cfg.allowed_user_ids:
        await cq.answer("Você não pode aprovar ações deste bot.", show_alert=True)
        return
    _, rid, action = cq.data.split(":")
    res = services.desk.resolve(rid, action)
    if res is None:
        await cq.answer("Pedido expirado ou já respondido.")
    elif res == "confirm":
        await cq.answer(
            "⚠️ Ação sensível. Toque em Aprovar de novo (até 20s) pra confirmar.", show_alert=True
        )
    else:
        await cq.answer("ok")


@router.callback_query(F.data.startswith("proj:"))
async def on_project_pick(cq: CallbackQuery, services: Services) -> None:
    if cq.from_user.id not in services.cfg.allowed_user_ids or cq.message is None:
        await cq.answer()
        return
    alias = cq.data.split(":", 1)[1]
    project = services.projects.get(alias)
    if project is None:
        await cq.answer("Projeto desconhecido")
        return
    msg = cq.message
    topic = (msg.message_thread_id or 0) if getattr(msg, "is_topic_message", None) else 0
    conv = services.store.ensure(msg.chat.id, topic, alias)
    conv.project = alias
    services.store.clear_session(conv)
    with contextlib.suppress(TelegramBadRequest):
        await msg.edit_text(f"📁 {alias} → {project.path} (sessão zerada)")
    await cq.answer("ok")


@router.stopped_message_generation()
async def on_stop_button(event: MessageGenerationStopped, services: Services) -> None:
    key = f"{event.chat.id}:{event.message_thread_id or 0}"
    active = services.state.active.get(key)
    log.info(
        "stop pelo botão: chat=%s thread=%s draft=%s ativo=%s",
        event.chat.id,
        event.message_thread_id,
        event.draft_id,
        active.turn.draft_id if active else None,
    )
    if active and active.turn.draft_id == event.draft_id:
        active.turn.stop()
