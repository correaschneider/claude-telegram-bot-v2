"""Descoberta de skills/commands do Claude Code e a tradução de nome pro menu do Telegram."""

import os
import tempfile

from tgclaude.core.skills import (
    Entry,
    build_registry,
    discover,
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
