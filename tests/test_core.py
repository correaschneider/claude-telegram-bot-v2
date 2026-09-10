"""Testes do núcleo com Claude e Telegram FALSOS. Roda standalone:
.venv/bin/python tests/test_core.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_stream import (  # noqa: E402
    Exited,
    Init,
    Result,
    RunSpec,
    TextDelta,
    ToolDone,
    ToolStart,
    build_args,
    parse_line,
)
from formatting import chunk_markdown, md_to_html  # noqa: E402
from sessions import SessionStore  # noqa: E402
from turn import Turn  # noqa: E402

CHAT = 123
DRAFT = 77


class FakeSink:
    def __init__(self) -> None:
        self.drafts: list[str] = []
        self.sent: list[str] = []

    async def draft(self, chat_id, draft_id, text):
        assert (chat_id, draft_id) == (CHAT, DRAFT)
        self.drafts.append(text)

    async def send(self, chat_id, markdown):
        assert chat_id == CHAT
        self.sent.append(markdown)


class FakeProc:
    """Emite eventos roteirizados; `None` no roteiro = pausa (simula Claude pensando)."""

    def __init__(self, script, pause=0.03):
        self._script = script
        self._pause = pause
        self.killed = False

    def kill(self):
        self.killed = True

    async def events(self):
        for item in self._script:
            if self.killed:
                break
            if item is None:
                await asyncio.sleep(self._pause)
            else:
                yield item
        yield Exited(0 if not self.killed else -15, "")


def make_turn(sink, interval=0.01, keepalive=0.05):
    return Turn(sink, CHAT, DRAFT, interval=interval, keepalive=keepalive, max_chars=3500)


# ---- parse_line ----


def test_parse_line():
    assert parse_line("") == []
    assert parse_line("não é json") == []
    assert parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "abc"})) == [
        Init("abc")
    ]
    delta = {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Oi"}},
    }
    assert parse_line(json.dumps(delta)) == [TextDelta("Oi")]
    thinking = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "x"},
        },
    }
    assert parse_line(json.dumps(thinking)) == []
    assistant = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "vou rodar"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls  -la\n/tmp"}},
            ]
        },
    }
    assert parse_line(json.dumps(assistant)) == [ToolStart("Bash", "ls -la /tmp")]
    user = {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}}
    assert parse_line(json.dumps(user)) == [ToolDone(True)]
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "fim",
        "session_id": "abc",
        "total_cost_usd": 0.5,
        "num_turns": 2,
        "permission_denials": [{"tool_name": "Bash"}],
    }
    assert parse_line(json.dumps(result)) == [Result("fim", False, "abc", 0.5, 2, 1)]
    err = {"type": "result", "subtype": "error_during_execution", "result": "boom"}
    assert parse_line(json.dumps(err))[0].is_error


def test_build_args():
    spec = RunSpec(
        "-começa com traço", "/w", "sid", False, "acceptEdits", "Read Bash(git:*)", ("/x",), "sys"
    )
    args = build_args("claude", spec)
    assert args[-2:] == ["--", "-começa com traço"]
    assert "--resume" in args and "sid" in args
    assert "--allowedTools" in args and "--dangerously-skip-permissions" not in args
    yolo = build_args("claude", RunSpec("p", "/w", None, True, "x", "y", (), "s"))
    assert "--dangerously-skip-permissions" in yolo and "--allowedTools" not in yolo
    assert "--include-partial-messages" in yolo and "--verbose" in yolo


# ---- turn ----


def test_turn_streams_and_finalizes():
    sink = FakeSink()
    script = [
        Init("sess-1"),
        TextDelta("Olá"),
        None,
        TextDelta(", mundo"),
        None,
        ToolStart("Bash", "ls"),
        None,
        ToolDone(False),
        TextDelta("Pronto."),
        None,
        Result("Pronto.", False, "sess-1", 0.12, 2, 0),
    ]
    outcome = asyncio.run(make_turn(sink).run(FakeProc(script)))

    assert sink.drafts[0] == "", "primeiro rascunho vazio = placeholder Thinking…"
    assert any("Olá, mundo" in d for d in sink.drafts)
    assert any("🔧 Bash · ls" in d for d in sink.drafts), sink.drafts
    assert outcome.session_id == "sess-1" and not outcome.error and not outcome.stopped
    assert len(sink.sent) == 1
    final = sink.sent[0]
    assert final.startswith("Olá, mundo\n\nPronto."), final  # separador após tool use
    assert "⏱" in final and "$0.12" in final and "2 turnos" in final
    assert "🔒" not in final


def test_turn_keepalive_without_events():
    sink = FakeSink()
    script = [
        Init("s"),
        TextDelta("x"),
        None,
        None,
        None,
        None,
        None,
        Result("x", False, "s", None, 1, 0),
    ]
    asyncio.run(make_turn(sink, interval=0.01, keepalive=0.02).run(FakeProc(script, pause=0.03)))
    # 5 pausas de 30ms com keepalive de 20ms → vários reenvios idênticos do mesmo texto
    assert sink.drafts.count("x") >= 3, sink.drafts


def test_turn_throttles():
    sink = FakeSink()
    script = [Init("s")] + [TextDelta("a")] * 200 + [Result("a" * 200, False, "s", None, 1, 0)]
    asyncio.run(make_turn(sink, interval=0.05, keepalive=1).run(FakeProc(script)))
    assert len(sink.drafts) <= 3, len(sink.drafts)  # placeholder + no máximo um ou dois flushes


def test_turn_stop_button():
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
    assert sink.sent[0].startswith("⏹ Interrompido.\n\nparcial"), sink.sent


def test_turn_error_and_denials():
    sink = FakeSink()
    asyncio.run(
        make_turn(sink).run(FakeProc([Init("s"), Result("deu ruim", True, "s", None, 1, 0)]))
    )
    assert sink.sent[0].startswith("❌ deu ruim")

    sink = FakeSink()
    asyncio.run(
        make_turn(sink).run(
            FakeProc([Init("s"), TextDelta("ok"), Result("ok", False, "s", 0.1, 1, 2)])
        )
    )
    assert "🔒 2 chamada(s)" in sink.sent[0]

    sink = FakeSink()
    asyncio.run(make_turn(sink).run(FakeProc([Init("s")])))  # morreu sem result
    assert sink.sent[0].startswith("❌ Claude encerrou sem resultado")


def test_render_sliding_window():
    turn = Turn(FakeSink(), CHAT, DRAFT, interval=1, keepalive=1, max_chars=10)
    turn._apply(TextDelta("0123456789ABCDEF"))
    assert turn.render() == "…6789ABCDEF"
    turn._apply(ToolStart("Read", "/x"))
    assert turn.render().endswith("\n\n🔧 Read · /x")


# ---- formatting ----


def test_md_to_html():
    out = md_to_html("## Título\ntexto **forte** com `a<b`\n```py\nx = 1 < 2\n```\nfim & cabo")
    assert "<b>Título</b>" in out
    assert "<b>forte</b>" in out
    assert "<code>a&lt;b</code>" in out
    assert "<pre>x = 1 &lt; 2</pre>" in out
    assert "fim &amp; cabo" in out
    assert md_to_html("```\naberto sem fechar") == "<pre>aberto sem fechar</pre>"


def test_chunk_markdown():
    assert chunk_markdown("") == [""]
    assert chunk_markdown("a\nb") == ["a\nb"]
    md = "intro\n```\n" + "\n".join(f"linha {i}" for i in range(40)) + "\n```\nfim"
    chunks = chunk_markdown(md, size=120)
    assert len(chunks) > 1
    for c in chunks:
        assert c.count("```") % 2 == 0, c  # fence fechado em cada pedaço
        assert len(c) <= 124
    assert "".join(chunk_markdown("x" * 500, size=100)) == "x" * 500


# ---- sessions ----


def test_session_store():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        store = SessionStore(path, ttl_seconds=1)
        assert store.get(CHAT) is None
        store.set(CHAT, "sid")
        assert SessionStore(path, 1).get(CHAT) == "sid"
        store._data[str(CHAT)]["updated_at"] = time.time() - 5
        assert store.get(CHAT) is None
        store.clear(CHAT)
        assert store.get(CHAT) is None


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"\n{len(tests)} testes passaram")
