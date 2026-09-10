"""Entrada multimídia: download de voz/imagem e contexto da mensagem respondida.

Imagem é entregue ao Claude por caminho no disco (a tool Read abre PNG/JPG). Ao
responder ("reply") a uma mensagem, o teor dela entra no prompt — inclusive o eco
`📝 Transcrito:` do próprio bot, que é fala do usuário."""

from __future__ import annotations

import logging
import os

from aiogram import Bot
from aiogram.types import Message

log = logging.getLogger("claude-bot")

TRANSCRIPT_HEADER = "📝 Transcrito:"
MAX_QUOTE_CHARS = 3000
_START = "=== INÍCIO DA MENSAGEM CITADA (o usuário usou 'responder' nela) ==="
_END = "=== FIM DA MENSAGEM CITADA ==="


def is_image(message: Message) -> bool:
    return bool(
        message.photo
        or (message.document and (message.document.mime_type or "").startswith("image/"))
    )


async def download_image(bot: Bot, message: Message, image_dir: str) -> str | None:
    if message.photo:
        ph = message.photo[-1]
        file_id, uid, ext = ph.file_id, ph.file_unique_id, ".jpg"
    elif message.document:
        doc = message.document
        file_id, uid = doc.file_id, doc.file_unique_id
        ext = os.path.splitext(doc.file_name or "")[1] or ".img"
    else:
        return None
    path = os.path.join(image_dir, f"{uid}{ext}")
    try:
        os.makedirs(image_dir, exist_ok=True)
        await bot.download(file_id, destination=path)
        return path
    except Exception as e:  # noqa: BLE001 — arquivo expirado/rede: segue sem imagem
        log.warning("falha baixando imagem: %s", e)
        return None


async def download_audio(bot: Bot, message: Message, audio_dir: str) -> str:
    media = message.voice or message.audio
    assert media is not None
    ext = (
        ".ogg"
        if message.voice
        else os.path.splitext(getattr(media, "file_name", "") or "")[1] or ".mp3"
    )
    path = os.path.join(audio_dir, f"{media.file_unique_id}{ext}")
    os.makedirs(audio_dir, exist_ok=True)
    await bot.download(media.file_id, destination=path)
    return path


def is_transcript_echo(message: Message, bot_id: int) -> bool:
    user = message.from_user
    return bool(
        user and user.id == bot_id and (message.text or "").lstrip().startswith(TRANSCRIPT_HEADER)
    )


def _author(message: Message, bot_id: int) -> str:
    user = message.from_user
    if user is None:
        return "canal/desconhecido"
    if is_transcript_echo(message, bot_id):
        return "o próprio usuário (transcrição de um áudio que ele mandou)"
    if user.id == bot_id:
        return "você mesmo (Claude, em um turno anterior)"
    if user.is_bot:
        return f"bot @{user.username or user.id}"
    return f"usuário {user.full_name}".strip()


def _media_note(message: Message) -> str:
    if message.voice:
        return "[a mensagem citada é um áudio de voz — não transcrito aqui]"
    if message.audio:
        return "[a mensagem citada é um arquivo de áudio]"
    if message.video or message.video_note:
        return "[a mensagem citada é um vídeo]"
    if message.sticker:
        return f"[a mensagem citada é um sticker {message.sticker.emoji or ''}]".strip()
    if message.location:
        return f"[localização: {message.location.latitude}, {message.location.longitude}]"
    if message.document:
        d = message.document
        return f"[documento anexado: {d.file_name or d.file_unique_id} ({d.mime_type or '?'})]"
    return ""


async def build_reply_context(bot: Bot, bot_id: int, message: Message, image_dir: str) -> str:
    """Bloco de contexto da mensagem respondida, ou "" se não for um reply."""
    quoted = message.reply_to_message
    if quoted is None:
        return ""
    lines = [_START, f"Autor: {_author(quoted, bot_id)}"]
    body = (quoted.text or quoted.caption or "").strip()
    if is_transcript_echo(quoted, bot_id):
        body = body[len(TRANSCRIPT_HEADER) :].strip()
    picked = (message.quote.text or "").strip() if message.quote else ""
    if picked:
        lines.append(f"Trecho que o usuário selecionou: {picked[:MAX_QUOTE_CHARS]}")
    if body:
        lines.append(f"Conteúdo: {body[:MAX_QUOTE_CHARS]}")
    if is_image(quoted):
        path = await download_image(bot, quoted, image_dir)
        lines.append(
            f"Imagem da mensagem citada (abra com a tool Read se for relevante): {path}"
            if path
            else "[a mensagem citada tem uma imagem que não pôde ser baixada]"
        )
    else:
        note = _media_note(quoted)
        if note:
            lines.append(note)
    if len(lines) == 2:
        lines.append("(mensagem citada sem texto)")
    lines.append(_END)
    return "\n".join(lines)


def with_reply_context(reply_block: str, prompt: str) -> str:
    if not reply_block:
        return prompt
    return f"{reply_block}\n\nMensagem NOVA do usuário (responda a esta):\n{prompt}"
