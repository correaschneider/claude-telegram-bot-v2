"""Atalho de argumento: comando pelado do menu vira ForceReply; a resposta vira o prompt."""

import asyncio
from types import SimpleNamespace

import pytest

from tgclaude.core.projects import ProjectRegistry
from tgclaude.services import RuntimeState
from tgclaude.telegram.handlers import skill_prompt


class FakeMessage:
    def __init__(self, text, *, reply_to=None, chat_id=1):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.reply_to_message = reply_to
        self.answered: list[tuple[str, object]] = []

    async def answer(self, text, **kw):
        self.answered.append((text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=999)


@pytest.fixture
def services(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "commands").mkdir(parents=True)
    (home / ".claude" / "commands" / "factory-dev.md").write_text(
        "---\ndescription: Dev\nargument-hint: <task-id>\n---\n", encoding="utf-8"
    )
    (home / ".claude" / "commands" / "hab-open.md").write_text(
        "# HABs\n\nsem args", encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    return SimpleNamespace(
        projects=ProjectRegistry.load(str(tmp_path / "nope.json"), str(tmp_path)),
        state=RuntimeState(),
        bot_username="ClaudeBot",
    )


CONV = SimpleNamespace(project="main")


def run(services, message):
    return asyncio.run(skill_prompt(services, CONV, message))


def test_texto_normal_passa_intacto(services):
    assert run(services, FakeMessage("oi, tudo bem?")) == "oi, tudo bem?"


def test_comando_com_argumento_so_traduz_o_nome(services):
    assert run(services, FakeMessage("/factory_dev 86ak 123")) == "/factory-dev 86ak 123"
    assert not services.state.pending_skill


def test_comando_pelado_que_pede_argumento_abre_force_reply(services):
    msg = FakeMessage("/factory_dev")
    assert run(services, msg) is None  # não roda turno
    text, markup = msg.answered[0]
    assert "/factory-dev" in text and "&lt;task-id&gt;" not in text
    assert markup.force_reply and markup.input_field_placeholder == "<task-id>"
    assert services.state.pending_skill == {(1, 999): "factory-dev"}


def test_resposta_ao_force_reply_vira_prompt(services):
    services.state.pending_skill[(1, 999)] = "factory-dev"
    reply = FakeMessage("86ak7", reply_to=SimpleNamespace(message_id=999))
    assert run(services, reply) == "/factory-dev 86ak7"
    assert not services.state.pending_skill  # consumido

    services.state.pending_skill[(1, 999)] = "factory-dev"
    pulou = FakeMessage("-", reply_to=SimpleNamespace(message_id=999))
    assert run(services, pulou) == "/factory-dev"


def test_comando_sem_argumento_roda_direto(services):
    msg = FakeMessage("/hab-open")
    assert run(services, msg) == "/hab-open" and not msg.answered
