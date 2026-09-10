"""Composition root: lê config, monta adaptadores, sobe bridge MCP, agendador e polling."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from dotenv import load_dotenv

import callbacks
import group
import handlers
from bridge_server import BridgeServer
from claude_stream import SubprocessRunner
from config import Config
from permission_desk import PermissionDesk
from permissions import PolicyLoader
from projects import ProjectRegistry
from questions import QuestionDesk
from scheduler import JobScheduler, JobStore
from services import Bridge, RuntimeState, Services
from store import ConversationStore
from transcriber import WhisperXTranscriber

log = logging.getLogger("claude-bot")
HERE = os.path.dirname(os.path.abspath(__file__))

COMMANDS = [
    BotCommand(command="status", description="Projeto, sessão, yolo, regras, agendamentos"),
    BotCommand(command="project", description="Trocar o projeto desta conversa"),
    BotCommand(command="new", description="Tópico novo: /new <alias> [título]"),
    BotCommand(command="fork", description="Bifurcar a sessão atual num tópico novo"),
    BotCommand(command="sessions", description="Retomar uma sessão recente do Claude Code"),
    BotCommand(command="reset", description="Zerar a sessão do Claude"),
    BotCommand(command="cancel", description="Interromper o turno em execução"),
    BotCommand(command="yolo", description="Pular aprovações por N min: /yolo [min]"),
    BotCommand(command="allow", description="Regras aprovadas aqui (/allow clear)"),
    BotCommand(command="audit", description="Decisões de permissão recentes → promover regras"),
    BotCommand(command="jobs", description="Agendamentos deste chat"),
    BotCommand(command="unschedule", description="Remover agendamento: /unschedule <id>"),
    BotCommand(command="link", description="Deep link: /link <alias|HT-123>"),
]


async def run() -> None:
    load_dotenv(os.path.join(HERE, ".env"))
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = Config.from_env()

    bot = Bot(cfg.telegram_token, default=DefaultBotProperties(parse_mode=None))
    me = await bot.get_me()
    store = ConversationStore(cfg.store_file, cfg.session_ttl)
    bridge_server = BridgeServer(cfg.bridge_host, cfg.bridge_port)
    url = await bridge_server.start()
    scheduler = JobScheduler(JobStore(cfg.jobs_file), cfg.default_tz)
    policy = PolicyLoader(cfg.permissions_file)

    services = Services(
        cfg=cfg,
        bot=bot,
        bot_username=me.username or "",
        bot_id=me.id,
        projects=ProjectRegistry.load(cfg.projects_file, cfg.workspace),
        policy=policy,
        store=store,
        runner=SubprocessRunner(cfg.claude_bin),
        state=RuntimeState(),
        bridge=Bridge(
            url=url,
            token=bridge_server.token,
            script=os.path.join(HERE, "mcp_bridge.py"),
            python=sys.executable,
        ),
        desk=PermissionDesk(bot, store, policy, cfg.decisions_file),
        questions=QuestionDesk(bot),
        scheduler=scheduler,
        transcriber=WhisperXTranscriber(cfg),
    )
    bridge_server.attach(services)
    scheduler.attach(services)

    dp = Dispatcher()
    dp["services"] = services
    dp.update.outer_middleware(
        handlers.AllowlistMiddleware(cfg.allowed_chat_ids, cfg.allowed_user_ids)
    )
    dp.include_routers(callbacks.router, handlers.router, group.router, group.guest_router)

    await bot.set_my_commands(COMMANDS)
    scheduler.start()
    log.info(
        "bot @%s no ar; projetos=%s; chats=%s; users=%s; topics=%s guest=%s; jobs=%d",
        me.username,
        [p.alias for p in services.projects.all()],
        sorted(cfg.allowed_chat_ids),
        sorted(cfg.allowed_user_ids),
        getattr(me, "has_topics_enabled", None),
        getattr(me, "supports_guest_queries", None),
        len(scheduler.store.jobs),
    )
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bridge_server.stop()


def main() -> None:
    asyncio.run(run())
