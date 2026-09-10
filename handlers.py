"""Handlers do chat privado (com ou sem tópicos): comandos e texto → turno."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from aiogram.utils.deep_linking import create_start_link

from formatting import format_elapsed
from projects import Project
from runner import is_busy, run_turn
from services import Services
from store import Conversation
from telegram_sink import TelegramSink

log = logging.getLogger("claude-bot")
router = Router(name="private")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class AllowlistMiddleware(BaseMiddleware):
    """Derruba qualquer update (mensagem, stop, callback, guest…) que não venha de um chat
    autorizado nem de um usuário autorizado."""

    def __init__(self, chats: frozenset[int], users: frozenset[int]) -> None:
        self._chats = chats
        self._users = users

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        chat_id, user_id = ids_of(event)
        if chat_id not in self._chats and user_id not in self._users:
            log.warning("update ignorado: chat=%s user=%s", chat_id, user_id)
            return None
        log.info("update %s chat=%s user=%s", event.event_type, chat_id, user_id)
        return await handler(event, data)


def ids_of(update: Update) -> tuple[int | None, int | None]:
    msg = update.message or update.edited_message or update.guest_message
    if msg:
        return msg.chat.id, msg.from_user.id if msg.from_user else None
    if update.stopped_message_generation:
        return update.stopped_message_generation.chat.id, None
    if update.callback_query:
        cq = update.callback_query
        return (cq.message.chat.id if cq.message else None), cq.from_user.id
    return None, None


def topic_of(message: Message) -> int:
    return (message.message_thread_id or 0) if message.is_topic_message else 0


def conversation_of(services: Services, message: Message) -> tuple[Conversation, bool]:
    """Conversa do (chat, tópico); cria com o projeto padrão se não existir."""
    chat_id, topic = message.chat.id, topic_of(message)
    existing = services.store.get(chat_id, topic)
    if existing:
        return existing, False
    return services.store.ensure(chat_id, topic, services.projects.default.alias), True


async def create_topic(
    services: Services,
    chat_id: int,
    project: Project,
    title: str,
    *,
    auto_title: bool,
    fork_from: str | None = None,
) -> Conversation:
    topic = await services.bot.create_forum_topic(chat_id, name=title[:128])
    conv = Conversation(
        chat_id=chat_id,
        topic_id=topic.message_thread_id,
        project=project.alias,
        fork_from=fork_from,
        auto_title=auto_title,
    )
    services.store.put(conv)
    return conv


TOPICS_HINT = (
    "Não consegui criar o tópico. Ligue *Topics in Private Chats* no @BotFather "
    "(/mybots → Bot Settings) e tente de novo."
)


def _projects_keyboard(services: Services) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{p.alias} · {p.path}"[:60], callback_data=f"proj:{p.alias}"
                )
            ]
            for p in services.projects.all()
        ]
    )


# ---- comandos ----


@router.message(CommandStart(deep_link=True, deep_link_encoded=True))
async def on_start_deep_link(message: Message, command: CommandObject, services: Services) -> None:
    payload = (command.args or "").strip()
    kind, _, rest = payload.partition(":")
    project: Project | None = None
    title = ""
    if kind == "p":
        alias, _, title = rest.partition(":")
        project = services.projects.get(alias)
    elif kind == "t":
        project = services.projects.for_task(rest)
        title = rest.upper()
    if project is None:
        await message.answer(f"Link desconhecido: {payload}")
        return
    try:
        conv = await create_topic(
            services, message.chat.id, project, title or project.alias, auto_title=not title
        )
    except TelegramBadRequest:
        conv, _ = conversation_of(services, message)
        conv.project = project.alias
        services.store.clear_session(conv)
        await message.answer(f"📁 Projeto desta conversa: {project.alias}\n\n{TOPICS_HINT}")
        return
    hint = f"Contexto: task {title}." if kind == "t" else ""
    await services.bot.send_message(
        conv.chat_id,
        f"📁 {project.alias} ({project.path})\n{hint}\nPode mandar.",
        message_thread_id=conv.topic_id,
    )


@router.message(CommandStart())
async def on_start(message: Message, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    await message.answer(
        "Bot v2 — streaming do Claude Code.\n"
        f"Projeto desta conversa: {conv.project}\n\n"
        "/project trocar · /new <alias> tópico novo · /fork bifurcar sessão\n"
        "/status · /reset · /cancel · /yolo [min] · /allow · /link"
    )


@router.message(Command("status"))
async def on_status(message: Message, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    project = services.projects.get_or_default(conv.project)
    active = services.state.active.get(conv.key)
    yolo = conv.yolo()
    lines = [
        f"📁 {project.alias} → {project.path}",
        f"🧵 tópico {conv.topic_id or '—'} · sessão {conv.session_id[:8] if conv.session_id else '— (nova)'}",
        f"🔓 yolo: {'ON até ' + time.strftime('%H:%M', time.localtime(conv.yolo_until)) if yolo else 'off'}",
        f"🔁 regras: {', '.join(conv.allow) if conv.allow else '—'}",
        f"⚙️ processando há {format_elapsed(time.monotonic() - active.started_at)}"
        if active
        else "💤 ocioso",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("reset"))
async def on_reset(message: Message, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    services.store.clear_session(conv)
    await message.answer("🧹 Sessão zerada. O próximo turno começa do zero.")


@router.message(Command("cancel"))
async def on_cancel(message: Message, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    active = services.state.active.get(conv.key)
    if not active:
        await message.answer("Nada rodando aqui.")
        return
    active.turn.stop()
    await message.answer("⏹ Cancelando…")


@router.message(Command("yolo"))
async def on_yolo(message: Message, command: CommandObject, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    if conv.yolo():
        conv.yolo_until = 0
        services.store.put(conv)
        await message.answer("🔒 yolo OFF — de volta às aprovações por botão.")
        return
    minutes = (
        int(command.args)
        if command.args and command.args.isdigit()
        else services.cfg.yolo_ttl // 60
    )
    conv.yolo_until = time.time() + minutes * 60
    services.store.put(conv)
    await message.answer(
        f"🔓 yolo ON por {minutes} min neste tópico — --dangerously-skip-permissions. /yolo desliga."
    )


@router.message(Command("project"))
async def on_project(message: Message, command: CommandObject, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    alias = (command.args or "").strip()
    if not alias:
        await message.answer(
            f"Projeto atual: {conv.project}. Escolha:", reply_markup=_projects_keyboard(services)
        )
        return
    project = services.projects.get(alias)
    if project is None:
        await message.answer(f"Projeto desconhecido: {alias}")
        return
    conv.project = alias
    services.store.clear_session(conv)
    await message.answer(f"📁 {alias} → {project.path} (sessão zerada)")


@router.message(Command("new"))
async def on_new(message: Message, command: CommandObject, services: Services) -> None:
    args = (command.args or "").split(maxsplit=1)
    if not args:
        await message.answer("Uso: /new <alias> [título]")
        return
    project = services.projects.get(args[0])
    if project is None:
        await message.answer(f"Projeto desconhecido: {args[0]}")
        return
    title = args[1] if len(args) > 1 else ""
    try:
        conv = await create_topic(
            services, message.chat.id, project, title or project.alias, auto_title=not title
        )
    except TelegramBadRequest as e:
        log.warning("createForumTopic falhou: %s", e)
        await message.answer(TOPICS_HINT, parse_mode="Markdown")
        return
    await services.bot.send_message(
        conv.chat_id,
        f"📁 {project.alias} ({project.path})\nPode mandar.",
        message_thread_id=conv.topic_id,
    )


@router.message(Command("fork"))
async def on_fork(message: Message, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    if not conv.session_id:
        await message.answer("Sem sessão ativa pra bifurcar. Mande algo primeiro.")
        return
    project = services.projects.get_or_default(conv.project)
    try:
        child = await create_topic(
            services,
            message.chat.id,
            project,
            f"{project.alias} · fork",
            auto_title=True,
            fork_from=conv.session_id,
        )
    except TelegramBadRequest:
        await message.answer(TOPICS_HINT, parse_mode="Markdown")
        return
    await services.bot.send_message(
        child.chat_id,
        f"🌿 Bifurcação da sessão {conv.session_id[:8]} ({project.alias}). O próximo turno continua daqui.",
        message_thread_id=child.topic_id,
    )


@router.message(Command("link"))
async def on_link(message: Message, command: CommandObject, services: Services) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Uso: /link <alias> ou /link HT-123")
        return
    if services.projects.get(arg):
        payload = f"p:{arg}"
    elif services.projects.for_task(arg):
        payload = f"t:{arg.upper()}"
    else:
        await message.answer(f"Nem projeto nem task com prefixo conhecido: {arg}")
        return
    link = await create_start_link(services.bot, payload, encode=True)
    await message.answer(f"🔗 {link}\n\nAbre o bot já num tópico de {arg}.")


@router.message(Command("allow"))
async def on_allow(message: Message, command: CommandObject, services: Services) -> None:
    conv, _ = conversation_of(services, message)
    if (command.args or "").strip() == "clear":
        conv.allow = []
        services.store.put(conv)
        await message.answer("🔁 Regras desta conversa apagadas.")
        return
    await message.answer(
        "🔁 Regras aprovadas nesta conversa:\n"
        + ("\n".join(conv.allow) if conv.allow else "—")
        + "\n\n/allow clear apaga."
    )


# ---- texto → turno ----


@router.message(F.text)
async def on_text(message: Message, services: Services) -> None:
    conv, created = conversation_of(services, message)
    if is_busy(services, conv):
        await message.answer("⏳ Ainda processando o turno anterior. /cancel pra interromper.")
        return
    if created and conv.topic_id:
        await message.answer(f"Tópico novo → projeto {conv.project}. /project troca.")
    try:
        await run_turn(
            services,
            conv,
            message.text or "",
            draft_id=message.message_id,
            sink=TelegramSink(services.bot, use_rich=services.cfg.rich_messages),
            actor_id=message.from_user.id if message.from_user else message.chat.id,
            allow_prompt=True,
        )
    except Exception:
        log.exception("turno falhou conv=%s", conv.key)
        await message.answer("❌ Falha interna ao rodar o turno; veja o log do bot.")


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Por enquanto só texto. Voz/imagem vêm depois.")
