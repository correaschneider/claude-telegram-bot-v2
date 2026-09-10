"""Checklist da tarefa: Rich Message com task list GFM, editada in-place a cada atualização.
(A checklist nativa do Telegram exige business_connection_id — só bots business.)"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage

from tgclaude.core.formatting import checklist_html, checklist_markdown
from tgclaude.core.store import Conversation
from tgclaude.services import Services

log = logging.getLogger("claude-bot")


async def update_checklist(
    services: Services, conv: Conversation, title: str, items: list[dict]
) -> str:
    bot = services.bot
    md = checklist_markdown(title, items)
    html_text = checklist_html(title, items)
    thread = conv.topic_id or None

    if conv.checklist_msg:
        try:
            if services.cfg.rich_messages:
                await bot.edit_message_text(
                    chat_id=conv.chat_id,
                    message_id=conv.checklist_msg,
                    rich_message=InputRichMessage(markdown=md),
                )
            else:
                await bot.edit_message_text(
                    html_text,
                    chat_id=conv.chat_id,
                    message_id=conv.checklist_msg,
                    parse_mode="HTML",
                )
            return "checklist atualizada"
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return "checklist sem mudanças"
            log.warning("edit da checklist falhou (%s); enviando nova", e)

    msg = None
    if services.cfg.rich_messages:
        try:
            msg = await bot.send_rich_message(
                conv.chat_id, rich_message=InputRichMessage(markdown=md), message_thread_id=thread
            )
        except TelegramBadRequest as e:
            log.warning("checklist rich rejeitada (%s); HTML", e)
    if msg is None:
        msg = await bot.send_message(
            conv.chat_id, html_text, parse_mode="HTML", message_thread_id=thread
        )
    conv.checklist_msg = msg.message_id
    services.store.put(conv)
    return "checklist criada"
