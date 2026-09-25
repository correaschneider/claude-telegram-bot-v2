"""Descoberta de skills/commands do Claude Code e a tradução de nome pro menu do Telegram."""

import os
import tempfile

from tgclaude.core.skills import (
    Entry,
    build_registry,
    discover,
    find_entry,
    render,
    rewrite_command,
    telegram_name,
)


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_telegram_name():
    assert telegram_name("factory-cr") == "factory_cr"
    assert telegram_name("trackers:jira") == "trackers_jira"
    assert telegram_name("  Deploy HML  ") == "deploy_hml"
    assert telegram_name("a" * 60) == "a" * 32
    assert telegram_name("---") == ""


def test_discover_frontmatter_heading_e_override_do_projeto():
    with tempfile.TemporaryDirectory() as d:
        home, proj = os.path.join(d, "home"), os.path.join(d, "proj")
        _write(
            f"{home}/.claude/skills/push/SKILL.md",
            "---\nname: push\ndescription: Rebase, MR e cherry-pick.\n---\n\ncorpo",
        )
        _write(f"{home}/.claude/commands/factory-cr.md", "# Factory CR — orquestrador\n\ntexto")
        _write(f"{home}/.claude/commands/vazio.md", "")
        _write(
            f"{proj}/.claude/skills/push/SKILL.md",
            "---\nname: push\ndescription: Versão do projeto.\n---\n",
        )
        entries = {e.name: e for e in discover(proj, home=home)}

        assert set(entries) == {"push", "factory-cr", "vazio"}
        assert entries["push"].scope == "project"  # projeto vence a global de mesmo nome
        assert entries["push"].description == "Versão do projeto."
        assert entries["factory-cr"].kind == "command"
        assert entries["factory-cr"].description == "Factory CR — orquestrador"
        assert entries["vazio"].description == "vazio"  # sem conteúdo → cai no nome
        assert entries["factory-cr"].tg_name == "factory_cr"
        assert discover(None, home=home) and all(
            e.scope == "global" for e in discover(None, home=home)
        )


def test_build_registry_pula_reservado_e_colisao():
    entries = [
        Entry("factory-cr", "d", "command", "global"),
        Entry("factory_cr", "outro", "command", "project"),  # mesmo tg_name
        Entry("status", "colide com o bot", "skill", "global"),
    ]
    registry = build_registry(entries, reserved={"status"})
    assert registry == {"factory_cr": "factory-cr"}


def test_rewrite_command():
    registry = {"factory_cr": "factory-cr", "cu": "cu"}
    assert rewrite_command("/factory_cr 86ak 1", registry) == "/factory-cr 86ak 1"
    assert rewrite_command("/factory_cr", registry) == "/factory-cr"
    assert rewrite_command("/cu 123", registry) == "/cu 123"  # sem hífen, segue igual
    assert rewrite_command("/FACTORY_CR x", registry) == "/factory-cr x"
    assert rewrite_command("/desconhecido x", registry) == "/desconhecido x"
    assert rewrite_command("texto normal", registry) == "texto normal"
    assert rewrite_command("/factory_cr@Bot x", registry, bot_username="Bot") == "/factory-cr x"


def test_render():
    entries = [
        Entry("deploy-hml", "Publica em HML. Detalhe extra.", "skill", "project"),
        Entry("push", "Rebase e MR", "skill", "global"),
    ]
    out = render(entries, "smsfunnel")
    assert "📁 smsfunnel" in out and "🌐 globais" in out
    assert "<code>/deploy-hml</code> (menu: /deploy_hml)" in out
    assert "<i>Publica em HML</i>" in out  # só a 1ª frase
    assert "<code>/push</code>\n" in out  # sem alias quando o nome já serve
    assert "deploy" not in render(entries, "smsfunnel", "push")
    assert "Nenhuma skill" in render(entries, "smsfunnel", "zzz")


def test_menu_description_e_takes_args():
    # nome repetido no começo, parêntese no fim e frase longa são aparados pro menu
    e = Entry(
        "factory-cr",
        "Factory CR — Orquestrador da fábrica de Code Review (genérico/config-driven)",
        "command",
        "global",
        takes_args=True,
    )
    assert e.menu_description == "Orquestrador da fábrica de Code Review"
    com_hint = Entry(
        "factory-dev", "Leva a task ao QA. Detalhe.", "command", "global", "<task-id>", True
    )
    assert com_hint.menu_description == "Leva a task ao QA · <task-id>"
    longa = Entry("x", "a" * 200, "skill", "global", "<id>")
    assert len(longa.menu_description) <= 62 and longa.menu_description.endswith("… · <id>")


def test_discover_le_argument_hint_e_arguments():
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        _write(
            f"{home}/.claude/commands/cu.md",
            "---\ndescription: Renomeia o chat\nargument-hint: <task_id>\n---\n\nUse $ARGUMENTS",
        )
        _write(
            f"{home}/.claude/commands/factory-cr.md", "# Factory CR\n\nRecebe $ARGUMENTS e revisa"
        )
        _write(f"{home}/.claude/commands/hab-open.md", "# HABs abertas\n\nsem argumento")
        by = {e.name: e for e in discover(None, home=home)}
        assert by["cu"].argument_hint == "<task_id>" and by["cu"].takes_args
        assert by["factory-cr"].takes_args and not by["factory-cr"].argument_hint
        assert not by["hab-open"].takes_args


def test_find_entry():
    entries = [Entry("factory-cr", "d", "command", "global")]
    assert find_entry(entries, "factory-cr") is entries[0]
    assert find_entry(entries, "factory_cr") is entries[0]  # pelo nome do menu
    assert find_entry(entries, "outro") is None
