"""Adaptador da porta DraftSink sobre o Bot do aiogram."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from formatting import chunk_markdown, md_to_html

log = logging.getLogger("claude-bot")


class TelegramSink:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def draft(self, chat_id: int, draft_id: int, text: str) -> None:
        # Rascunho é best-effort: falha aqui não pode derrubar o turno.
        try:
            await self._bot.send_message_draft(
                chat_id=chat_id,
                draft_id=draft_id,
                text=text,
                can_stop=True,
                keep_on_stop=False,
            )
        except TelegramRetryAfter as e:
            log.warning("draft rate-limited: retry em %ss", e.retry_after)
        except TelegramBadRequest as e:
            log.warning("draft rejeitado: %s", e)

    async def send(self, chat_id: int, markdown: str) -> None:
        for chunk in chunk_markdown(markdown):
            try:
                await self._bot.send_message(chat_id, md_to_html(chunk), parse_mode="HTML")
            except TelegramBadRequest as e:
                log.warning("HTML rejeitado (%s); enviando texto puro", e)
                await self._bot.send_message(chat_id, chunk, parse_mode=None)
