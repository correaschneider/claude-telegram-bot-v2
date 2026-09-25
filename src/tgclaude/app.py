"""Composition root: lê config, monta adaptadores, sobe bridge MCP, agendador e polling."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from dotenv import load_dotenv

from tgclaude.claude.stream import SubprocessRunner
from tgclaude.config import Config
from tgclaude.core.permissions import PolicyLoader
from tgclaude.core.projects import ProjectRegistry
from tgclaude.core.skills import build_registry, discover
from tgclaude.core.store import ConversationStore
from tgclaude.services import Bridge, RuntimeState, Services
from tgclaude.telegram import callbacks, group, handlers
from tgclaude.telegram.transcriber import WhisperXTranscriber
from tgclaude.tools.permission_desk import PermissionDesk
from tgclaude.tools.questions import QuestionDesk
from tgclaude.tools.scheduler import JobScheduler, JobStore
from tgclaude.tools.server import BridgeServer

log = logging.getLogger("claude-bot")


MAX_MENU_COMMANDS = 100  # teto do setMyCommands


def menu_commands(project_path: str | None) -> list[BotCommand]:
    """Comandos do bot + skills/commands GLOBAIS do Claude Code (o menu é por chat, não por
    tópico, então o que é de projeto fica só no /skills)."""
    reserved = {c.command for c in handlers.COMMANDS}
    entries = [e for e in discover(project_path) if e.scope == "global"]
    by_name = {e.name: e for e in entries}
    extra = [
        BotCommand(command=tg, description=by_name[real].description[:256])
        for tg, real in build_registry(entries, reserved=reserved).items()
    ]
    return (handlers.COMMANDS + extra)[:MAX_MENU_COMMANDS]


def bridge_script_path() -> str:
    """Caminho do mcp_bridge.py sem importá-lo (ele lê TG_* do ambiente ao carregar)."""
    spec = importlib.util.find_spec("tgclaude.claude.mcp_bridge")
    assert spec and spec.origin
    return spec.origin


async def run() -> None:
    # .env fica na raiz do repo (= WorkingDirectory do serviço), não dentro do pacote.
    load_dotenv(os.environ.get("TGCLAUDE_ENV") or os.path.join(os.getcwd(), ".env"))
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
            script=bridge_script_path(),
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
    dp.include_routers(
        callbacks.router, handlers.router, group.router, group.guest_router, group.channel_router
    )

    commands = menu_commands(services.projects.default.path)
    await bot.set_my_commands(commands)
    scheduler.start()
    log.info(
        "bot @%s no ar; projetos=%s; chats=%s; users=%s; topics=%s guest=%s; jobs=%d; menu=%d",
        me.username,
        [p.alias for p in services.projects.all()],
        sorted(cfg.allowed_chat_ids),
        sorted(cfg.allowed_user_ids),
        getattr(me, "has_topics_enabled", None),
        getattr(me, "supports_guest_queries", None),
        len(scheduler.store.jobs),
        len(commands),
    )
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bridge_server.stop()


def main() -> None:
    asyncio.run(run())
