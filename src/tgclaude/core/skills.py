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
_FIELD = re.compile(r"^(name|description|argument-hint)\s*:\s*(.+?)\s*$", re.M)
_TAKES_ARGS = re.compile(r"\$ARGUMENTS|\$\d\b")
MAX_MENU_DESCRIPTION = 62  # acima disso o cliente trunca a descrição no menu
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
    argument_hint: str = ""  # frontmatter `argument-hint`; "" quando não declara
    takes_args: bool = False  # hint declarado ou $ARGUMENTS/$1 no corpo

    @property
    def tg_name(self) -> str:
        return telegram_name(self.name)

    @property
    def menu_description(self) -> str:
        """Curta o bastante pro menu do Telegram não cortar, com o argumento no fim."""
        text = self.description
        # "Factory CR — Orquestrador da fábrica…" → tira o nome repetido
        prefix = re.match(r"^(.{2,40}?)\s+[—–-]\s+", text)
        if prefix and telegram_name(prefix.group(1)) == self.tg_name:
            text = text[prefix.end() :]
        text = re.split(r"(?<=[.;])\s", text, maxsplit=1)[0].rstrip(".")
        text = re.sub(r"\s*\([^()]*\)\s*$", "", text)  # parêntese no fim não informa nada
        hint = f" · {self.argument_hint}" if self.argument_hint else ""
        room = MAX_MENU_DESCRIPTION - len(hint)
        if len(text) > room:
            text = text[: room - 1].rstrip(" ,;:-") + "…"
        return (text + hint)[:MAX_TG_DESCRIPTION]


def _summarize(text: str, fallback: str) -> tuple[str, str, str, bool]:
    """(nome, descrição, argument-hint, pede argumento) de um SKILL.md/command.md."""
    name, description, hint = fallback, "", ""
    m = _FRONTMATTER.search(text)
    if m:
        fields = dict(_FIELD.findall(m.group(1)))
        name = fields.get("name", fallback).strip().strip("\"'") or fallback
        description = fields.get("description", "").strip().strip("\"'")
        hint = fields.get("argument-hint", "").strip().strip("\"'")
        text = text[m.end() :]
    if not description:
        for line in text.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line
                break
    description = " ".join(description.split())
    return name, description[:MAX_TG_DESCRIPTION], hint[:60], bool(hint or _TAKES_ARGS.search(text))


def _read(path: str, fallback: str, kind: str, scope: str) -> Entry | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(8000)
    except OSError as e:
        log.warning("não li %s: %s", path, e)
        return None
    name, description, hint, takes_args = _summarize(text, fallback)
    if not telegram_name(name):
        return None
    return Entry(
        name=name,
        description=description or fallback,
        kind=kind,
        scope=scope,
        argument_hint=hint,
        takes_args=takes_args,
    )


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


def find_entry(entries: list[Entry], tg_or_real_name: str) -> Entry | None:
    needle = tg_or_real_name.lower()
    return next((e for e in entries if needle in (e.name.lower(), e.tg_name)), None)


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
            arg = f" <b>{e.argument_hint}</b>" if e.argument_hint else ""
            lines.append(f"<code>/{e.name}</code>{arg}{alias}\n   <i>{short}</i>")
    lines.append(
        "\nToque no nome pra copiar. Nome com hífen só entra no menu com <code>_</code>, "
        "e o bot traduz de volta."
    )
    return "\n".join(lines).strip()
