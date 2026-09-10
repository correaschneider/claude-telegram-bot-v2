"""Entrega de arquivos gerados pelo Claude.

1. AUTO-DETECÇÃO: caminhos absolutos de MÍDIA de saída (imagem, áudio, vídeo, PDF/SVG)
   citados no texto são enviados — esses tipos raramente aparecem por acaso.
2. EXPLÍCITA (tool `send_file`): qualquer arquivo, inclusive .md/.json/.txt, que não
   entram na auto-detecção porque o Claude cita caminhos de fonte o tempo todo."""

from __future__ import annotations

import logging
import os
import re

from aiogram import Bot
from aiogram.types import FSInputFile

log = logging.getLogger("claude-bot")

PHOTO_EXT = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
DOC_AUTO_EXT = {".pdf", ".svg", ".gif"}
AUTO_EXT = PHOTO_EXT | AUDIO_EXT | VIDEO_EXT | DOC_AUTO_EXT

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_FILES_PER_TURN = 10

_ext_alt = "|".join(sorted(e[1:] for e in AUTO_EXT))
SENDABLE_RE = re.compile(rf"/[^\s'\"`<>|()\[\]]+\.(?:{_ext_alt})\b", re.IGNORECASE)


async def _send_one(bot: Bot, chat_id: int, thread_id: int | None, path: str) -> bool:
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    ext = os.path.splitext(path)[1].lower()
    as_photo = ext in PHOTO_EXT and size <= MAX_PHOTO_BYTES
    if not as_photo and size > MAX_DOC_BYTES:
        await bot.send_message(
            chat_id,
            f"⚠️ {path} ({size // (1024 * 1024)} MB) excede o limite do Telegram.",
            message_thread_id=thread_id,
        )
        return False
    file = FSInputFile(path)
    try:
        if as_photo:
            await bot.send_photo(chat_id, file, message_thread_id=thread_id)
        elif ext in AUDIO_EXT:
            await bot.send_audio(chat_id, file, message_thread_id=thread_id)
        elif ext in VIDEO_EXT:
            await bot.send_video(chat_id, file, message_thread_id=thread_id)
        else:
            await bot.send_document(chat_id, file, message_thread_id=thread_id)
        return True
    except Exception as e:  # noqa: BLE001 — container recusado etc.: tenta como documento
        log.warning("falha enviando %s: %s", path, e)
        if ext in AUDIO_EXT or ext in VIDEO_EXT or as_photo:
            try:
                await bot.send_document(bot_chat := chat_id, file, message_thread_id=thread_id)
                return True
            except Exception as e2:  # noqa: BLE001
                log.warning("fallback documento falhou %s (%s): %s", path, bot_chat, e2)
        return False


async def send_paths(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    paths: list[str],
    *,
    sent: set[str] | None = None,
    warn_missing: bool = False,
) -> set[str]:
    """Envia caminhos únicos, na ordem, no máximo MAX_FILES_PER_TURN por turno."""
    sent = sent if sent is not None else set()
    for path in dict.fromkeys(paths):
        if path in sent or len(sent) >= MAX_FILES_PER_TURN:
            continue
        if not os.path.isfile(path):
            if warn_missing:
                await bot.send_message(
                    chat_id, f"⚠️ arquivo não encontrado: {path}", message_thread_id=thread_id
                )
            continue
        if await _send_one(bot, chat_id, thread_id, path):
            sent.add(path)
    return sent


async def send_generated_files(
    bot: Bot, chat_id: int, thread_id: int | None, text: str, *, sent: set[str] | None = None
) -> set[str]:
    return await send_paths(bot, chat_id, thread_id, SENDABLE_RE.findall(text), sent=sent)
