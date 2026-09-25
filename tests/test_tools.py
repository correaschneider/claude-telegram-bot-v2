"""Entrega de arquivos, reply-context, agendador, índice de sessões."""

from __future__ import annotations

from tgclaude.core.store import Conversation


def test_delivery_media_regex():
    from tgclaude.tools.delivery import SENDABLE_RE

    text = "gerei /tmp/out/grafico.png e o relatório em /data/x/rel.pdf; fonte: /src/app.py e `/tmp/a b.jpg`"
    assert SENDABLE_RE.findall(text) == ["/tmp/out/grafico.png", "/data/x/rel.pdf"]


def test_reply_context_pure():
    from tgclaude.telegram.media import with_reply_context

    assert with_reply_context("", "oi") == "oi"
    assert with_reply_context("BLOCO", "oi").startswith("BLOCO\n\nMensagem NOVA")


def test_scheduler_validate_and_describe():
    from tgclaude.tools.scheduler import build_trigger, describe, validate

    j = validate(
        {"prompt": "resumo", "cron": "0 9 * * 1-5", "title": "Resumo"}, "America/Sao_Paulo"
    )
    assert (
        j["cron"] == "0 9 * * 1-5"
        and j["tz"] == "America/Sao_Paulo"
        and describe(j).startswith("0 9 * * 1-5")
    )
    assert build_trigger(j) is not None
    e = validate({"prompt": "ping", "every_seconds": 30}, "UTC")
    assert e["every_seconds"] == 30 and describe(e) == "a cada 30s (UTC)"
    for bad in (
        {"prompt": ""},
        {"prompt": "x"},
        {"prompt": "x", "every_seconds": 1},
        {"prompt": "x", "cron": "abc"},
        {"prompt": "x", "cron": "* * * * *", "tz": "Marte/Base"},
    ):
        try:
            validate(bad, "UTC")
        except ValueError:
            pass
        else:
            raise AssertionError(f"deveria falhar: {bad}")


def test_sessions_encode_and_job_conversation_key():
    from tgclaude.claude.sessions_index import encode_cwd

    assert encode_cwd("/data/projects") == "-data-projects"
    assert Conversation(1, 2, "x").key == "1:2"
    assert Conversation(1, 2, "x", job_id="7").key == "1:2:job7"


def test_rename_topic():
    import asyncio
    from types import SimpleNamespace

    from tgclaude.tools.server import TOOLS

    calls, saved = [], []
    bot = SimpleNamespace(
        edit_forum_topic=lambda chat, thread, name=None: (
            calls.append((chat, thread, name)) or asyncio.sleep(0)
        )
    )
    services = SimpleNamespace(bot=bot, store=SimpleNamespace(put=saved.append))
    conv = SimpleNamespace(chat_id=1, topic_id=7, auto_title=True, key="1:7")
    active = SimpleNamespace(conv=conv)
    run = TOOLS["rename_topic"]

    text, err = asyncio.run(run(services, active, {"title": "  CU-86ak  |  título \n da task "}))
    assert not err and calls == [(1, 7, "CU-86ak | título da task")]
    assert conv.auto_title is False and saved == [conv]  # runner não sobrescreve depois

    assert asyncio.run(run(services, active, {"title": "   "}))[1] is True
    fora = SimpleNamespace(conv=SimpleNamespace(chat_id=1, topic_id=0, auto_title=False, key="1:0"))
    text, err = asyncio.run(run(services, fora, {"title": "x"}))
    assert err and "não é um tópico" in text
    assert len(calls) == 1  # nenhuma chamada extra ao Telegram
