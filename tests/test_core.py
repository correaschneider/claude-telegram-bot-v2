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

from aiohttp import web  # noqa: E402

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
from formatting import (  # noqa: E402
    checklist_markdown,
    chunk_markdown,
    md_to_html,
    rich_markdown,
)
from permissions import (  # noqa: E402
    READ_ONLY_RULES,
    any_matches,
    decide,
    is_dangerous,
    matches,
    rule_for,
    split_rules,
)
from projects import ProjectRegistry  # noqa: E402
from store import Conversation, ConversationStore  # noqa: E402
from turn import Turn, TurnOutcome  # noqa: E402

CHAT = 123
THREAD = 9
DRAFT = 77
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSink:
    def __init__(self) -> None:
        self.drafts: list[str] = []
        self.sent: list[TurnOutcome] = []

    async def draft(self, chat_id, thread_id, draft_id, text):
        assert (chat_id, thread_id, draft_id) == (CHAT, THREAD, DRAFT)
        self.drafts.append(text)

    async def send(self, chat_id, thread_id, outcome):
        assert (chat_id, thread_id) == (CHAT, THREAD)
        self.sent.append(outcome)


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
    return Turn(sink, CHAT, THREAD, DRAFT, interval=interval, keepalive=keepalive, max_chars=3500)


# ---- parse_line / build_args ----


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
        "result": "fim",
        "session_id": "abc",
        "total_cost_usd": 0.5,
        "num_turns": 2,
        "permission_denials": [{"tool_name": "Bash"}],
    }
    assert parse_line(json.dumps(result)) == [Result("fim", False, "abc", 0.5, 2, 1)]
    assert parse_line(json.dumps({"type": "result", "subtype": "error_during_execution"}))[
        0
    ].is_error


def test_build_args():
    spec = RunSpec(
        "-começa com traço",
        "/w",
        "sid",
        False,
        "acceptEdits",
        "Read Bash(git:*)",
        ("/x",),
        "sys",
        fork_session=True,
        mcp_config='{"mcpServers":{}}',
        permission_prompt_tool="mcp__tg__ask",
        settings_json='{"permissions":{"ask":["Bash"]}}',
    )
    args = build_args("claude", spec)
    assert args[-2:] == ["--", "-começa com traço"]
    assert "--resume" in args and "sid" in args and "--fork-session" in args
    assert "--permission-prompt-tool" in args and "mcp__tg__ask" in args
    assert "--mcp-config" in args and "--permission-prompts" not in args
    assert args[args.index("--settings") + 1].startswith('{"permissions"')
    yolo = build_args("claude", RunSpec("p", "/w", None, True, "x", "y", (), "s"))
    assert "--dangerously-skip-permissions" in yolo and "--allowedTools" not in yolo
    group = build_args(
        "claude", RunSpec("p", "/w", None, False, "x", "y", (), "s", permission_prompts_none=True)
    )
    assert group[group.index("--permission-prompts") + 1] == "none"


# ---- turn ----


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
        Result("Pronto.", False, "sess-1", 0.12, 2, 0),
    ]
    outcome = asyncio.run(make_turn(sink).run(FakeProc(script)))

    assert sink.drafts[0] == "", "primeiro rascunho vazio = placeholder Thinking…"
    assert any("Vou olhar." in d for d in sink.drafts)
    assert any("🔧 Bash · ls" in d for d in sink.drafts), sink.drafts
    assert outcome.session_id == "sess-1" and not outcome.error and not outcome.stopped
    assert outcome.text == "Pronto: 2 arquivos." and outcome.progress == "Vou olhar."
    assert outcome.steps == 1
    assert "⏱" in outcome.footer and "0.12 USD" in outcome.footer and "2 turnos" in outcome.footer
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


def test_render_sliding_window():
    turn = Turn(FakeSink(), CHAT, THREAD, DRAFT, interval=1, keepalive=1, max_chars=10)
    turn._apply(TextDelta("0123456789ABCDEF"))
    assert turn.render() == "…6789ABCDEF"
    turn._apply(ToolStart("Read", "/x"))
    assert turn.render().endswith("\n\n🔧 Read · /x")


# ---- formatting ----


def test_md_to_html_and_chunks():
    out = md_to_html("## Título\ntexto **forte** com `a<b`\n```py\nx = 1 < 2\n```\nfim & cabo")
    assert "<b>Título</b>" in out and "<b>forte</b>" in out
    assert (
        "<code>a&lt;b</code>" in out
        and "<pre>x = 1 &lt; 2</pre>" in out
        and "fim &amp; cabo" in out
    )
    md = "intro\n```\n" + "\n".join(f"linha {i}" for i in range(40)) + "\n```\nfim"
    chunks = chunk_markdown(md, size=120)
    assert len(chunks) > 1 and all(c.count("```") % 2 == 0 for c in chunks)


def test_rich_markdown_and_checklist():
    o = TurnOutcome("resposta", "passo 1\n\npasso 2", 2, "⏱ 3s · 0.10 USD", "s", False, False, 3.0)
    rich = rich_markdown(o)
    assert rich.startswith("<details><summary>🔎 Progresso (2 ferramentas)</summary>")
    assert "\n\nresposta\n\n_⏱ 3s · 0.10 USD_" in rich
    assert rich_markdown(TurnOutcome("só", "", 0, "", None, False, False, 1)) == "só"
    md = checklist_markdown("Plano", [{"text": "a", "done": True}, {"text": "b\nc"}])
    assert md == "**Plano** · 1/2\n- [x] a\n- [ ] b c"


# ---- permissions ----


def test_permissions():
    assert rule_for("Bash", {"command": "git push origin main"}) == "Bash(git push *)"
    assert rule_for("Bash", {"command": "ls -la"}) == "Bash(ls *)"
    assert rule_for("Edit", {"file_path": "/x"}) == "Edit"
    assert matches("Bash(git push *)", "Bash", {"command": "git push origin dev"})
    assert not matches("Bash(git push *)", "Bash", {"command": "git pushy"})
    assert matches("Bash(git:*)", "Bash", {"command": "git status"})
    assert matches("Edit", "Edit", {"file_path": "/x"}) and not matches("Edit", "Write", {})
    assert any_matches(["Read", "Bash(ls *)"], "Bash", {"command": "ls"})
    assert is_dangerous("Bash", {"command": "git push origin main"})
    assert is_dangerous("Bash", {"command": "mysql -e 'DROP TABLE x'"})
    assert not is_dangerous("Bash", {"command": "git status"})
    assert not is_dangerous("Edit", {"file_path": "/x"})
    assert split_rules("Read Bash(git:*) Bash(git diff *)  mcp__x__y") == [
        "Read",
        "Bash(git:*)",
        "Bash(git diff *)",
        "mcp__x__y",
    ]
    rules = [*READ_ONLY_RULES, "Bash(git:*)"]
    assert decide(rules, [], "Bash", {"command": "ls -la"}, can_prompt=True) == "allow"
    assert decide(rules, [], "Bash", {"command": "git status"}, can_prompt=True) == "allow"
    assert decide(rules, [], "Bash", {"command": "git push origin x"}, can_prompt=True) == "prompt"
    assert (
        decide(
            rules, ["Bash(git push *)"], "Bash", {"command": "git push origin x"}, can_prompt=True
        )
        == "allow"
    )
    assert decide(rules, [], "Bash", {"command": "make build"}, can_prompt=False) == "deny"
    assert decide(rules, [], "Bash", {"command": "make build"}, can_prompt=True) == "prompt"


# ---- store / projects ----


def test_store_and_projects():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        store = ConversationStore(path, ttl_seconds=1)
        assert store.get(CHAT, 0) is None
        conv = store.ensure(CHAT, THREAD, "main")
        store.set_session(conv, "sid")
        conv.allow.append("Bash(git *)")
        conv.yolo_until = time.time() + 60
        store.put(conv)
        again = ConversationStore(path, 1).get(CHAT, THREAD)
        assert (
            again and again.session_id == "sid" and again.allow == ["Bash(git *)"] and again.yolo()
        )
        again.updated_at = time.time() - 5
        store2 = ConversationStore(path, 1)
        store2._data[again.key] = again
        assert store2.get(CHAT, THREAD).session_id is None  # TTL expirou, projeto fica
        store.clear_session(conv)
        assert store.get(CHAT, THREAD).session_id is None

        pj = os.path.join(d, "projects.json")
        with open(pj, "w") as f:
            json.dump(
                {
                    "a": {"path": "/a"},
                    "b": {"path": "/b", "default": True, "tasks": ["ht"], "chats": [-5]},
                },
                f,
            )
        reg = ProjectRegistry.load(pj, "/fallback")
        assert reg.default.alias == "b" and reg.get_or_default("zzz").alias == "b"
        assert reg.for_task("HT-123").alias == "b" and reg.for_task("CU-1") is None
        assert reg.for_chat(-5).alias == "b" and reg.for_chat(-6) is None
        assert ProjectRegistry.load(os.path.join(d, "nope.json"), "/fb").default.path == "/fb"
        assert Conversation(1, 0, "x").key == "1:0"


# ---- bridge MCP (processo real do mcp_bridge.py contra um HTTP falso) ----


def test_mcp_bridge_protocol():
    received: list[dict] = []

    async def ask(request):
        received.append(await request.json())
        assert request.headers["X-Bridge-Token"] == "tok"
        return web.json_response({"behavior": "allow", "updatedInput": {"command": "ls"}})

    async def checklist(request):
        received.append(await request.json())
        return web.json_response({"status": "checklist criada"})

    async def scenario():
        app = web.Application()
        app.router.add_post("/ask", ask)
        app.router.add_post("/checklist", checklist)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        env = {
            **os.environ,
            "TG_BRIDGE_URL": f"http://127.0.0.1:{port}",
            "TG_BRIDGE_TOKEN": "tok",
            "TG_TURN": "t1",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            os.path.join(ROOT, "mcp_bridge.py"),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        reqs = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ask",
                    "arguments": {
                        "tool_name": "Bash",
                        "input": {"command": "ls"},
                        "tool_use_id": "u1",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "update_checklist", "arguments": {"items": [{"text": "a"}]}},
            },
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ]
        proc.stdin.write("".join(json.dumps(r) + "\n" for r in reqs).encode())
        await proc.stdin.drain()
        responses = {}
        while len(responses) < 5:
            line = await asyncio.wait_for(proc.stdout.readline(), 10)
            msg = json.loads(line)
            responses[msg["id"]] = msg
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 5)
        await runner.cleanup()
        return responses

    r = asyncio.run(scenario())
    assert (
        r[1]["result"]["protocolVersion"] == "2025-06-18"
        and "tools" in r[1]["result"]["capabilities"]
    )
    assert {t["name"] for t in r[2]["result"]["tools"]} == {"ask", "update_checklist"}
    decision = json.loads(r[3]["result"]["content"][0]["text"])
    assert decision == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert r[4]["result"]["content"][0]["text"] == "checklist criada"
    assert r[5]["result"] == {}
    assert received[0]["turn"] == "t1" and received[0]["tool_name"] == "Bash"
    assert received[1]["items"] == [{"text": "a"}]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"\n{len(tests)} testes passaram")
