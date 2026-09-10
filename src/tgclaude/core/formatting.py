"""Apresentação: Markdown do Claude → Rich Markdown / HTML do Telegram, chunking, tempo."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tgclaude.core.turn import TurnOutcome

TELEGRAM_MAX = 4096
CHUNK_SIZE = 3800
RICH_MAX = 32768
RICH_CHUNK_SIZE = 30000

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


# ---- HTML (fallback / mensagens simples) ----


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"


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


# ---- renderização do resultado ----


def plain_markdown(outcome: TurnOutcome) -> str:
    """Tudo em sequência (progresso + resposta + rodapé), sem recursos ricos."""
    parts = [p for p in (outcome.progress, outcome.text, outcome.footer) if p]
    return "\n\n".join(parts)


def rich_markdown(outcome: TurnOutcome) -> str:
    """Rich Markdown do Telegram: progresso colapsado em <details>, rodapé em itálico."""
    parts: list[str] = []
    if outcome.progress:
        parts.append(
            f"<details><summary>🔎 Progresso ({outcome.steps} ferramentas)</summary>\n\n"
            f"{outcome.progress}\n\n</details>"
        )
    parts.append(outcome.text)
    if outcome.footer:
        parts.append("\n".join(f"_{line}_" for line in outcome.footer.split("\n")))
    return "\n\n".join(parts)


def checklist_markdown(title: str, items: list[dict]) -> str:
    done = sum(1 for i in items if i.get("done"))
    lines = [f"**{title or 'Checklist'}** · {done}/{len(items)}"]
    for i in items:
        text = str(i.get("text", "")).replace("\n", " ").strip()
        lines.append(f"- [{'x' if i.get('done') else ' '}] {text}")
    return "\n".join(lines)


def checklist_html(title: str, items: list[dict]) -> str:
    done = sum(1 for i in items if i.get("done"))
    lines = [f"<b>{html.escape(title or 'Checklist', quote=False)}</b> · {done}/{len(items)}"]
    for i in items:
        text = html.escape(str(i.get("text", "")).replace("\n", " ").strip(), quote=False)
        lines.append(f"✅ <s>{text}</s>" if i.get("done") else f"⬜ {text}")
    return "\n".join(lines)
