"""Núcleo do turno: rascunho, throttle, keepalive, stop, resultado."""

from __future__ import annotations

import asyncio

from helpers import CHAT, DRAFT, THREAD, FakeProc, FakeSink, make_turn

from tgclaude.claude.stream import (
    Init,
    Result,
    TextDelta,
    ToolDone,
    ToolStart,
)
from tgclaude.core.formatting import (
    fmt_tokens,
)
from tgclaude.core.turn import Turn


def test_turn_streams_and_finalizes():
    sink = FakeSink()
    script = [
        Init("sess-1"),
        TextDelta("Vou olhar."),
        None,
        ToolStart("Bash", "ls"),
        None,
        ToolDone(False),
        TextDelta("Pronto: 2 arquivos."),
        None,
        Result("Pronto.", False, "sess-1", 0.12, 2, 0, 153783, 163),
    ]
    outcome = asyncio.run(make_turn(sink).run(FakeProc(script)))

    assert sink.drafts[0] == "", "primeiro rascunho vazio = placeholder Thinking…"
    assert any("Vou olhar." in d for d in sink.drafts)
    assert any("🔧 Bash · ls" in d for d in sink.drafts), sink.drafts
    assert outcome.session_id == "sess-1" and not outcome.error and not outcome.stopped
    assert outcome.text == "Pronto: 2 arquivos." and outcome.progress == "Vou olhar."
    assert outcome.steps == 1
    assert (
        "⏱" in outcome.footer
        and "↓153.8k ↑163 tokens" in outcome.footer
        and "2 turnos" in outcome.footer
    )
    assert "USD" not in outcome.footer
    assert "🔒" not in outcome.footer
    assert sink.sent == [outcome]


def test_turn_keepalive_and_status():
    sink = FakeSink()
    script = [
        Init("s"),
        TextDelta("x"),
        None,
        None,
        None,
        None,
        Result("x", False, "s", None, 1, 0),
    ]
    turn = make_turn(sink, interval=0.01, keepalive=0.02)

    async def scenario():
        task = asyncio.create_task(turn.run(FakeProc(script, pause=0.03)))
        await asyncio.sleep(0.05)
        turn.set_status("🔐 aguardando")
        return await task

    asyncio.run(scenario())
    assert sink.drafts.count("x") >= 2, sink.drafts
    assert any(d.endswith("\n\n🔐 aguardando") for d in sink.drafts), sink.drafts


def test_turn_throttles():
    sink = FakeSink()
    script = [Init("s")] + [TextDelta("a")] * 200 + [Result("a" * 200, False, "s", None, 1, 0)]
    asyncio.run(make_turn(sink, interval=0.05, keepalive=1).run(FakeProc(script)))
    assert len(sink.drafts) <= 3, len(sink.drafts)


def test_turn_stop():
    sink = FakeSink()
    proc = FakeProc(
        [Init("s"), TextDelta("parcial"), None, None, None, TextDelta("nunca")], pause=0.05
    )
    turn = make_turn(sink)

    async def scenario():
        task = asyncio.create_task(turn.run(proc))
        await asyncio.sleep(0.06)
        turn.stop()
        return await task

    outcome = asyncio.run(scenario())
    assert proc.killed and outcome.stopped
    assert outcome.text.startswith("⏹ Interrompido.\n\nparcial"), outcome.text


def test_turn_errors_and_denials():
    sink = FakeSink()
    asyncio.run(
        make_turn(sink).run(FakeProc([Init("s"), Result("deu ruim", True, "s", None, 1, 0)]))
    )
    assert sink.sent[0].text.startswith("❌ deu ruim") and sink.sent[0].error

    sink = FakeSink()
    asyncio.run(
        make_turn(sink).run(
            FakeProc([Init("s"), TextDelta("ok"), Result("ok", False, "s", 0.1, 1, 2)])
        )
    )
    assert "🔒 2 chamada(s)" in sink.sent[0].footer

    sink = FakeSink()
    asyncio.run(make_turn(sink).run(FakeProc([Init("s")])))
    assert sink.sent[0].text.startswith("❌ Claude encerrou sem resultado")


def test_footer_with_cost():
    sink = FakeSink()
    turn = Turn(
        sink, CHAT, THREAD, DRAFT, interval=0.01, keepalive=1, max_chars=100, show_cost=True
    )
    asyncio.run(
        turn.run(
            FakeProc([Init("s"), TextDelta("x"), Result("x", False, "s", 0.12, 1, 0, 1500, 20)])
        )
    )
    assert "↓1.5k ↑20 tokens · 0.12 USD" in sink.sent[0].footer, sink.sent[0].footer
    assert (
        fmt_tokens(999) == "999" and fmt_tokens(1000) == "1k" and fmt_tokens(2_450_000) == "2.45M"
    )


def test_render_sliding_window():
    turn = Turn(FakeSink(), CHAT, THREAD, DRAFT, interval=1, keepalive=1, max_chars=10)
    turn._apply(TextDelta("0123456789ABCDEF"))
    assert turn.render() == "…6789ABCDEF"
    turn._apply(ToolStart("Read", "/x"))
    assert turn.render().endswith("\n\n🔧 Read · /x")
