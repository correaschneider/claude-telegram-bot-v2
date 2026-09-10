"""Perguntas com botões: a tool MCP `ask_user` vira um teclado inline (escolha única ou
múltipla) e bloqueia até a resposta — mesmo padrão da mesa de permissões."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import secrets
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import ActiveTurn

log = logging.getLogger("claude-bot")

MAX_OPTIONS = 10


@dataclass
class PendingQuestion:
    active: ActiveTurn
    options: list[str]
    multi: bool
    message_id: int
    selected: set[int] = field(default_factory=set)
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


def _keyboard(
    rid: str, options: list[str], multi: bool, selected: set[int]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(("✅ " if i in selected else "▫️ ") if multi else "") + opt[:56],
                callback_data=f"q:{rid}:{i}",
            )
        ]
        for i, opt in enumerate(options)
    ]
    if multi:
        rows.append([InlineKeyboardButton(text="✔️ Confirmar", callback_data=f"q:{rid}:ok")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class QuestionDesk:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self.pending: dict[str, PendingQuestion] = {}

    async def ask(self, active: ActiveTurn, question: str, options: list[str], multi: bool) -> str:
        options = [str(o).strip() for o in options if str(o).strip()][:MAX_OPTIONS]
        if not options:
            return "erro: nenhuma opção"
        conv = active.conv
        rid = secrets.token_hex(4)
        text = f"❓ {html.escape(question)}"
        msg = await self._bot.send_message(
            conv.chat_id,
            text,
            parse_mode="HTML",
            message_thread_id=conv.topic_id or None,
            reply_markup=_keyboard(rid, options, multi, set()),
        )
        pending = PendingQuestion(active, options, multi, msg.message_id)
        self.pending[rid] = pending
        active.turn.set_status("❓ aguardando sua resposta")
        try:
            answer: list[int] = await pending.future
        except asyncio.CancelledError:
            answer = []
        finally:
            self.pending.pop(rid, None)
            active.turn.set_status("")
        chosen = [options[i] for i in sorted(answer)]
        label = ", ".join(chosen) if chosen else "(sem resposta — turno cancelado)"
        with contextlib.suppress(TelegramBadRequest):
            await self._bot.edit_message_text(
                f"{text}\n→ {html.escape(label)}",
                chat_id=conv.chat_id,
                message_id=msg.message_id,
                parse_mode="HTML",
            )
        return label

    async def resolve(self, rid: str, action: str) -> str | None:
        p = self.pending.get(rid)
        if p is None or p.future.done():
            return None
        if action == "ok":
            if not p.multi:
                return None
            p.future.set_result(sorted(p.selected))
            return "ok"
        i = int(action)
        if i >= len(p.options):
            return None
        if not p.multi:
            p.future.set_result([i])
            return "ok"
        p.selected ^= {i}
        with contextlib.suppress(TelegramBadRequest):
            await self._bot.edit_message_reply_markup(
                chat_id=p.active.conv.chat_id,
                message_id=p.message_id,
                reply_markup=_keyboard(rid, p.options, True, p.selected),
            )
        return "toggle"

    def cancel_all(self, active: ActiveTurn) -> None:
        for p in list(self.pending.values()):
            if p.active is active and not p.future.done():
                p.future.cancel()
