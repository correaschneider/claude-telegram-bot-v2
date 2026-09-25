"""Descoberta das skills e commands do Claude Code no disco.

O Claude Code expande `/nome` no prompt do modo `-p`, então o bot só precisa (a) achar o que
existe e (b) traduzir o nome: o Telegram aceita apenas `[a-z0-9_]{1,32}` em comando, e skill
costuma ter hífen (`factory-cr`) ou namespace de plugin (`trackers:jira`).

Puro: lê arquivos e devolve dados — não conhece Telegram nem aiogram.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger("claude-bot")

MAX_TG_NAME = 32
MAX_TG_DESCRIPTION = 256
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_FIELD = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.M)
_INVALID = re.compile(r"[^a-z0-9_]+")


def telegram_name(name: str) -> str:
    """`factory-cr` → `factory_cr`; `trackers:jira` → `trackers_jira`. '' se não sobrar nada."""
    slug = _INVALID.sub("_", name.strip().lower()).strip("_")
    return slug[:MAX_TG_NAME]


@dataclass(frozen=True)
class Entry:
    name: str  # nome real, como o Claude Code espera: factory-cr
    description: str
    kind: str  # "skill" | "command"
    scope: str  # "global" | "project"

    @property
    def tg_name(self) -> str:
        return telegram_name(self.name)


def _summarize(text: str, fallback: str) -> tuple[str, str]:
    """(nome, descrição) de um SKILL.md/command.md: frontmatter quando há, senão 1ª linha útil."""
    name, description = fallback, ""
    m = _FRONTMATTER.search(text)
    if m:
        fields = dict(_FIELD.findall(m.group(1)))
        name = fields.get("name", fallback).strip().strip("\"'") or fallback
        description = fields.get("description", "").strip().strip("\"'")
        text = text[m.end() :]
    if not description:
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line
                break
    description = " ".join(description.split())
    return name, description[:MAX_TG_DESCRIPTION]


def _read(path: str, fallback: str, kind: str, scope: str) -> Entry | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(8000)
    except OSError as e:
        log.warning("não li %s: %s", path, e)
        return None
    name, description = _summarize(text, fallback)
    if not telegram_name(name):
        return None
    return Entry(name=name, description=description or fallback, kind=kind, scope=scope)


def _scan(base: str, scope: str) -> list[Entry]:
    """`<base>/skills/*/SKILL.md` + `<base>/commands/*.md` (símlink resolvido pelo glob)."""
    out: list[Entry] = []
    for path in sorted(glob.glob(os.path.join(base, "skills", "*", "SKILL.md"))):
        entry = _read(path, os.path.basename(os.path.dirname(path)), "skill", scope)
        if entry:
            out.append(entry)
    for path in sorted(glob.glob(os.path.join(base, "commands", "*.md"))):
        entry = _read(path, os.path.splitext(os.path.basename(path))[0], "command", scope)
        if entry:
            out.append(entry)
    return out


def discover(project_path: str | None = None, *, home: str | None = None) -> list[Entry]:
    """Globais (`CLAUDE_CONFIG_DIR` ou `~/.claude`) + as do projeto; projeto vence no empate."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        home or os.path.expanduser("~"), ".claude"
    )
    entries = _scan(config_dir, "global")
    if project_path:
        entries += _scan(os.path.join(project_path, ".claude"), "project")
    by_name: dict[str, Entry] = {}
    for entry in entries:  # projeto vem depois → sobrescreve a global de mesmo nome
        by_name[entry.name] = entry
    return sorted(by_name.values(), key=lambda e: e.name)


def build_registry(entries: list[Entry], *, reserved: set[str] | None = None) -> dict[str, str]:
    """`{tg_name: nome real}`, pulando o que colide com comando do bot ou com outra skill."""
    reserved = reserved or set()
    registry: dict[str, str] = {}
    for entry in entries:
        tg = entry.tg_name
        if tg in reserved:
            log.info("skill %s ignorada: colide com comando do bot", entry.name)
            continue
        if tg in registry:
            log.info("skill %s ignorada: %s já ocupa /%s", entry.name, registry[tg], tg)
            continue
        registry[tg] = entry.name
    return registry


def rewrite_command(text: str, registry: dict[str, str], *, bot_username: str = "") -> str:
    """`/factory_cr 123` → `/factory-cr 123`. Texto sem comando conhecido volta intacto."""
    if not text.startswith("/"):
        return text
    head, _, rest = text.partition(" ")
    token = head[1:]
    if bot_username and token.lower().endswith(f"@{bot_username.lower()}"):
        token = token[: -(len(bot_username) + 1)]
    real = registry.get(token.lower())
    if not real:
        return text
    return f"/{real} {rest}".rstrip()


def render(entries: list[Entry], project_alias: str, filter_text: str = "") -> str:
    """Lista em HTML pro Telegram; `<code>` é tap-to-copy no cliente."""
    needle = filter_text.strip().lower()
    if needle:
        entries = [
            e for e in entries if needle in e.name.lower() or needle in e.description.lower()
        ]
    if not entries:
        return (
            f"Nenhuma skill encontrada para <b>{needle}</b>."
            if needle
            else "Nenhuma skill encontrada."
        )

    lines: list[str] = []
    for scope, title in (("project", f"📁 {project_alias}"), ("global", "🌐 globais")):
        group = [e for e in entries if e.scope == scope]
        if not group:
            continue
        lines.append(f"\n<b>{title}</b>")
        for e in group:
            short = e.description.split(". ")[0][:110].rstrip(".")
            alias = f" (menu: /{e.tg_name})" if e.tg_name != e.name else ""
            lines.append(f"<code>/{e.name}</code>{alias}\n   <i>{short}</i>")
    lines.append(
        "\nToque no nome pra copiar. Nome com hífen só entra no menu com <code>_</code>, "
        "e o bot traduz de volta."
    )
    return "\n".join(lines).strip()
