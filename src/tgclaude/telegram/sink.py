"""Adaptadores da porta DraftSink sobre o Bot do aiogram.

- TelegramSink: chat privado (com ou sem tópico) — rascunho vivo + Rich Message final.
- ReplySink: grupos e guest mode — sem rascunho (a API só aceita em chat privado),
  "digitando…" como sinal de vida, resposta efêmera/pública/guest no final."""

from __future__ import annotations

import contextlib
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    EphemeralMessageParameters,
    InlineQueryResultArticle,
    InputRichMessage,
    InputTextMessageContent,
)

from tgclaude.core.formatting import (
    RICH_CHUNK_SIZE,
    chunk_markdown,
    md_to_html,
    plain_markdown,
    rich_markdown,
)
from tgclaude.core.turn import TurnOutcome

log = logging.getLogger("claude-bot")


async def send_markdown(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    rich: str,
    plain: str,
    *,
    use_rich: bool = True,
    ephemeral: EphemeralMessageParameters | None = None,
) -> None:
    """Tenta Rich Message; cai pra HTML e, se ainda falhar, texto puro."""
    if use_rich:
        try:
            for chunk in chunk_markdown(rich, RICH_CHUNK_SIZE):
                await bot.send_rich_message(
                    chat_id,
                    rich_message=InputRichMessage(markdown=chunk),
                    message_thread_id=thread_id,
                    ephemeral_message_parameters=ephemeral,
                )
            return
        except TelegramBadRequest as e:
            log.warning("rich message rejeitada (%s); caindo pra HTML", e)
    for chunk in chunk_markdown(plain):
        try:
            await bot.send_message(
                chat_id,
                md_to_html(chunk),
                parse_mode="HTML",
                message_thread_id=thread_id,
                ephemeral_message_parameters=ephemeral,
            )
        except TelegramBadRequest as e:
            log.warning("HTML rejeitado (%s); enviando texto puro", e)
            await bot.send_message(
                chat_id,
                chunk,
                parse_mode=None,
                message_thread_id=thread_id,
                ephemeral_message_parameters=ephemeral,
            )


class TelegramSink:
    def __init__(self, bot: Bot, *, use_rich: bool = True) -> None:
        self._bot = bot
        self._use_rich = use_rich

    async def draft(self, chat_id: int, thread_id: int | None, draft_id: int, text: str) -> None:
        # Rascunho é best-effort: falha aqui não pode derrubar o turno.
        try:
            await self._bot.send_message_draft(
                chat_id=chat_id,
                draft_id=draft_id,
                message_thread_id=thread_id,
                text=text,
                can_stop=True,
                keep_on_stop=False,
            )
        except TelegramRetryAfter as e:
            log.warning("draft rate-limited: retry em %ss", e.retry_after)
        except TelegramBadRequest as e:
            log.warning("draft rejeitado: %s", e)

    async def send(self, chat_id: int, thread_id: int | None, outcome: TurnOutcome) -> None:
        await send_markdown(
            self._bot,
            chat_id,
            thread_id,
            rich_markdown(outcome),
            plain_markdown(outcome),
            use_rich=self._use_rich,
        )


class ReplySink:
    def __init__(
        self,
        bot: Bot,
        *,
        receiver_user_id: int | None = None,
        guest_query_id: str | None = None,
        use_rich: bool = True,
    ) -> None:
        self._bot = bot
        self._receiver = receiver_user_id
        self._guest = guest_query_id
        self._use_rich = use_rich
        self._last_typing = 0.0

    async def draft(self, chat_id: int, thread_id: int | None, draft_id: int, text: str) -> None:
        if self._guest or time.monotonic() - self._last_typing < 4:
            return
        self._last_typing = time.monotonic()
        with contextlib.suppress(TelegramBadRequest):
            await self._bot.send_chat_action(chat_id, "typing", message_thread_id=thread_id)

    async def send(self, chat_id: int, thread_id: int | None, outcome: TurnOutcome) -> None:
        plain = plain_markdown(outcome)
        if self._guest:
            # Guest mode: exatamente uma resposta por interação, via answerGuestQuery.
            body = chunk_markdown(plain)[0]
            await self._bot.answer_guest_query(
                self._guest,
                result=InlineQueryResultArticle(
                    id="1",
                    title="Resposta",
                    input_message_content=InputTextMessageContent(
                        message_text=md_to_html(body), parse_mode="HTML"
                    ),
                ),
            )
            return
        ephemeral = (
            EphemeralMessageParameters(receiver_user_id=self._receiver) if self._receiver else None
        )
        await send_markdown(
            self._bot,
            chat_id,
            thread_id,
            rich_markdown(outcome),
            plain,
            use_rich=self._use_rich,
            ephemeral=ephemeral,
        )
