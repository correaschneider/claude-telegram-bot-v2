"""Composition root: lê config, monta adaptadores e sobe o polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from dotenv import load_dotenv

from claude_stream import SubprocessRunner
from config import Config
from handlers import AllowlistMiddleware, router
from services import RuntimeState, Services
from sessions import SessionStore
from telegram_sink import TelegramSink

log = logging.getLogger("claude-bot")

COMMANDS = [
    BotCommand(command="status", description="Sessão, yolo e se está processando"),
    BotCommand(command="reset", description="Zera a sessão do Claude"),
    BotCommand(command="cancel", description="Interrompe o turno em execução"),
    BotCommand(command="yolo", description="Liga/desliga --dangerously-skip-permissions"),
]


async def run() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = Config.from_env()

    bot = Bot(cfg.telegram_token, default=DefaultBotProperties(parse_mode=None))
    services = Services(
        cfg=cfg,
        runner=SubprocessRunner(cfg.claude_bin),
        sink=TelegramSink(bot),
        sessions=SessionStore(cfg.sessions_file, cfg.session_ttl),
        state=RuntimeState(),
    )

    dp = Dispatcher()
    dp["services"] = services
    dp.update.outer_middleware(AllowlistMiddleware(cfg.allowed_chat_ids))
    dp.include_router(router)

    await bot.set_my_commands(COMMANDS)
    me = await bot.get_me()
    log.info(
        "bot @%s no ar; workspace=%s; chats=%s",
        me.username,
        cfg.workspace,
        sorted(cfg.allowed_chat_ids),
    )
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    asyncio.run(run())
