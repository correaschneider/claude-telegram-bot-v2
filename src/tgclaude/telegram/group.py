"""Grupos e guest mode: responde só a menção/reply, sem rascunho (a API não permite),
resposta efêmera pro autor por padrão, pública com a tag configurada. Nunca aprova
permissão em grupo — o que não estiver em allowed_tools do projeto é negado."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from tgclaude.claude.runner import is_busy, run_turn
from tgclaude.services import Services
from tgclaude.telegram.sink import ReplySink

log = logging.getLogger("claude-bot")
router = Router(name="group")
router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))


def _prompt_from(message: Message, services: Services) -> tuple[str, bool] | None:
    """(prompt, público) se a mensagem é pra este bot; None caso contrário."""
    text = message.text or message.caption or ""
    mention = re.compile(rf"@{re.escape(services.bot_username)}\b", re.I)
    # Com Group Privacy ligado (default) o Telegram NÃO entrega menções — só comandos e
    # replies ao bot. Por isso `/ask …` (ou `/claude …`) também dispara.
    command = re.compile(rf"^/(ask|claude)(?:@{re.escape(services.bot_username)})?\b", re.I)
    replied = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == services.bot_id
    )
    if not (mention.search(text) or command.match(text) or replied or message.guest_query_id):
        return None
    prompt = mention.sub("", command.sub("", text, count=1)).strip()
    tag = services.cfg.group_public_tag
    public = bool(tag) and tag in prompt
    if public:
        prompt = prompt.replace(tag, "").strip()
    return prompt, public


async def _handle(message: Message, services: Services) -> None:
    if not message.from_user or message.from_user.id not in services.cfg.allowed_user_ids:
        return
    parsed = _prompt_from(message, services)
    if not parsed or not parsed[0]:
        return
    prompt, public = parsed
    topic = (message.message_thread_id or 0) if message.is_topic_message else 0
    project = services.projects.for_chat(message.chat.id) or services.projects.default
    conv = services.store.ensure(message.chat.id, topic, project.alias)
    if is_busy(services, conv):
        if not message.guest_query_id:
            await message.reply("⏳ Ainda processando o pedido anterior deste grupo.")
        return
    sink = ReplySink(
        services.bot,
        receiver_user_id=None if public else message.from_user.id,
        guest_query_id=message.guest_query_id,
        reply_to_message_id=message.message_id,
        use_rich=services.cfg.rich_messages,
    )
    try:
        await run_turn(
            services,
            conv,
            prompt,
            draft_id=message.message_id,
            sink=sink,
            actor_id=message.from_user.id,
            allow_prompt=False,
        )
    except Exception:
        log.exception("turno de grupo falhou conv=%s", conv.key)


@router.message(F.text | F.caption)
async def on_group_message(message: Message, services: Services) -> None:
    await _handle(message, services)


guest_router = Router(name="guest")


@guest_router.guest_message()
async def on_guest_message(message: Message, services: Services) -> None:
    await _handle(message, services)
