"""Tradução do nome do menu pro nome real da skill (sem interceptar argumento)."""

from types import SimpleNamespace

from tgclaude.core.projects import ProjectRegistry
from tgclaude.services import RuntimeState
from tgclaude.telegram.handlers import skill_prompt

CONV = SimpleNamespace(project="main")


def _services(tmp_path, monkeypatch):
    home = tmp_path / "home" / ".claude" / "commands"
    home.mkdir(parents=True)
    (home / "factory-dev.md").write_text(
        "---\ndescription: Dev\nargument-hint: <task-id>\n---\n", encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / ".claude"))
    return SimpleNamespace(
        projects=ProjectRegistry.load(str(tmp_path / "nope.json"), str(tmp_path)),
        state=RuntimeState(),
        bot_username="ClaudeBot",
    )


def test_traduz_nome_do_menu_e_preserva_o_resto(tmp_path, monkeypatch):
    services = _services(tmp_path, monkeypatch)
    assert skill_prompt(services, CONV, "/factory_dev 86ak 1") == "/factory-dev 86ak 1"
    # sem argumento vai pelado pro Claude: quem pergunta (com botões) é a skill
    assert skill_prompt(services, CONV, "/factory_dev") == "/factory-dev"
    assert skill_prompt(services, CONV, "/factory_dev@ClaudeBot x") == "/factory-dev x"
    assert skill_prompt(services, CONV, "/desconhecido x") == "/desconhecido x"
    assert skill_prompt(services, CONV, "oi") == "oi"
