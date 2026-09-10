"""Apresentação: Markdown do Claude → HTML do Telegram, quebra em chunks, tempo humano."""

from __future__ import annotations

import html
import re

TELEGRAM_MAX = 4096
CHUNK_SIZE = 3800

_FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = _INLINE_CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _HEADING.sub(r"<b>\1</b>", text)
    return text


def md_to_html(md: str) -> str:
    """Conversão mínima e à prova de falha: só code fence, `code`, **bold** e headings."""
    parts: list[str] = []
    pos = 0
    for m in _FENCE.finditer(md):
        parts.append(_inline(md[pos : m.start()]))
        parts.append(f"<pre>{html.escape(m.group(1).rstrip(), quote=False)}</pre>")
        pos = m.end()
    parts.append(_inline(md[pos:]))
    return "".join(parts)


def chunk_markdown(md: str, size: int = CHUNK_SIZE) -> list[str]:
    """Quebra por linha respeitando `size`; fecha/reabre code fence na fronteira."""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    in_fence = False

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n".join(cur))
        cur, cur_len = [], 0

    for line in md.split("\n"):
        while len(line) > size:  # linha gigante: corte duro
            flush()
            chunks.append(line[:size])
            line = line[size:]
        extra = len(line) + (1 if cur else 0)
        if cur_len + extra > size:
            if in_fence:
                cur.append("```")
            flush()
            if in_fence:
                cur, cur_len = ["```"], 3
        cur.append(line)
        cur_len += extra
        if line.startswith("```"):
            in_fence = not in_fence
    flush()
    return chunks or [""]
