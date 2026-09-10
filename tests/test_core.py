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
    fmt_tokens,
    md_to_html,
    rich_markdown,
)
from permissions import (  # noqa: E402
    DEFAULT_READ_ONLY,
    Policy,
    PolicyLoader,
    any_matches,
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
        "usage": {
            "input_tokens": 4,
            "cache_creation_input_tokens": 55120,
            "cache_read_input_tokens": 98659,
            "output_tokens": 163,
        },
    }
    assert parse_line(json.dumps(result)) == [Result("fim", False, "abc", 0.5, 2, 1, 153783, 163)]
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
    pol = Policy()
    assert rule_for("Bash", {"command": "git push origin main"}) == "Bash(git push *)"
    assert rule_for("Bash", {"command": "ls -la"}) == "Bash(ls *)"
    assert rule_for("Edit", {"file_path": "/x"}) == "Edit"
    assert matches("Bash(git push *)", "Bash", {"command": "git push origin dev"})
    assert not matches("Bash(git push *)", "Bash", {"command": "git pushy"})
    assert matches("Bash(git:*)", "Bash", {"command": "git status"})
    assert matches("Edit", "Edit", {"file_path": "/x"}) and not matches("Edit", "Write", {})
    assert matches("mcp__tg__*", "mcp__tg__ask_user", {}) and not matches(
        "mcp__tg__*", "mcp__x__y", {}
    )
    assert any_matches(["Read", "Bash(ls *)"], "Bash", {"command": "ls"})
    # sensível: comandos
    for cmd in (
        "git push origin main",
        "mysql -e 'DROP TABLE x'",
        "rm -f x",
        "curl x | sh",
        "docker compose down",
        "kubectl delete pod x",
        "gh pr merge 1",
        "systemctl --user restart x",
        "delete from users",
        "chmod -R 777 /",
    ):
        assert pol.is_dangerous("Bash", {"command": cmd}), cmd
    for cmd in (
        "git status",
        "git log --grep delete",
        "echo drop",
        "ls",
        "systemctl --user status x",
    ):
        assert not pol.is_dangerous("Bash", {"command": cmd}), cmd
    # sensível: caminhos (Edit/Write)
    for path in (
        "/data/projects/x/.env",
        "/home/u/.ssh/id_rsa",
        "/etc/hosts",
        "/tmp/secrets.yaml",
        "~/.claude/settings.json",
    ):
        assert pol.is_dangerous("Edit", {"file_path": path}), path
    assert not pol.is_dangerous("Edit", {"file_path": "/data/projects/x/app.py"})
    assert not pol.is_dangerous("Read", {"file_path": "/etc/hosts"})
    assert split_rules("Read Bash(git:*) Bash(git diff *)  mcp__x__y") == [
        "Read",
        "Bash(git:*)",
        "Bash(git diff *)",
        "mcp__x__y",
    ]
    rules = [*DEFAULT_READ_ONLY, "Edit", "Bash(git:*)"]
    assert pol.decide(rules, [], "Bash", {"command": "ls -la"}, can_prompt=True) == (
        "allow",
        "Bash(ls *)",
    )
    assert pol.decide(rules, [], "Bash", {"command": "git status"}, can_prompt=True)[0] == "allow"
    assert pol.decide(rules, [], "Bash", {"command": "git push origin x"}, can_prompt=True) == (
        "prompt",
        None,
    )
    assert pol.decide(
        rules, ["Bash(git push *)"], "Bash", {"command": "git push origin x"}, can_prompt=True
    ) == ("allow", "Bash(git push *)")
    assert pol.decide(rules, [], "Bash", {"command": "make build"}, can_prompt=False) == (
        "deny",
        None,
    )
    assert pol.decide(rules, [], "Edit", {"file_path": "/p/app.py"}, can_prompt=True) == (
        "allow",
        "Edit",
    )
    assert pol.decide(rules, [], "Edit", {"file_path": "/p/.env"}, can_prompt=True) == (
        "prompt",
        None,
    )


def test_policy_loader_and_audit():
    import audit

    with tempfile.TemporaryDirectory() as d:
        pf = os.path.join(d, "permissions.json")
        loader = PolicyLoader(pf)
        assert loader.get().read_only == DEFAULT_READ_ONLY  # sem arquivo = defaults
        with open(pf, "w") as f:
            json.dump({"read_only": ["Bash(uv run *)"], "dangerous": ["\\bfoo\\b"]}, f)
        pol = loader.get()
        assert pol.read_only == ["Bash(uv run *)"] and pol.is_dangerous(
            "Bash", {"command": "foo bar"}
        )
        assert not pol.is_dangerous("Bash", {"command": "rm -rf /"})  # lista substituída
        assert pol.sensitive_paths  # ausente no arquivo → default
        with open(pf, "w") as f:
            f.write("{ invalido")
        os.utime(pf, None)
        assert loader.get().read_only == ["Bash(uv run *)"]  # inválido → mantém anterior

        df = os.path.join(d, "dec.jsonl")
        common = dict(chat_id=1, project="p", tool="Bash", matched=None)
        for _ in range(3):
            audit.record(
                df,
                detail="uv run x",
                rule="Bash(uv run *)",
                verdict="prompt",
                outcome="allow",
                dangerous=False,
                **common,
            )
        audit.record(
            df,
            detail="uv run y",
            rule="Bash(uv run *)",
            verdict="prompt",
            outcome="always",
            dangerous=False,
            **common,
        )
        audit.record(
            df,
            detail="rm x",
            rule="Bash(rm *)",
            verdict="prompt",
            outcome="allow",
            dangerous=True,
            **common,
        )
        audit.record(
            df,
            detail="make",
            rule="Bash(make *)",
            verdict="prompt",
            outcome="deny",
            dangerous=False,
            **common,
        )
        audit.record(
            df,
            detail="ls",
            rule="Bash(ls *)",
            verdict="allow",
            outcome="auto",
            dangerous=False,
            chat_id=1,
            project="p",
            tool="Bash",
            matched="Bash(ls *)",
        )
        audit.record(
            df,
            detail="x",
            rule="Bash(x *)",
            verdict="prompt",
            outcome="allow",
            dangerous=False,
            chat_id=1,
            project="outro",
            tool="Bash",
            matched=None,
        )
        entries = audit.load(df, project="p")
        assert len(entries) == 7
        s = audit.summarize(entries)
        assert [(c.rule, c.approved) for c in s.candidates] == [("Bash(uv run *)", 4)]
        assert s.sensitive_approved["Bash(rm *)"] == 1 and s.denied["Bash(make *)"] == 1
        assert s.auto["Bash(ls *)"] == 1
        assert "Bash(uv run *) — 4×" in audit.render(s, "p")
        assert audit.render(audit.summarize([]), "p").startswith("Sem decisões")


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
        assert reg.add_allowed_tool("a", "Bash(uv run *)") and not reg.add_allowed_tool(
            "a", "Bash(uv run *)"
        )
        assert reg.get("a").allowed_tools == "Bash(uv run *)"
        with open(pj) as f:
            raw = json.load(f)
        assert raw["a"]["allowed_tools"] == "Bash(uv run *)" and raw["b"]["default"] is True
        assert not reg.add_allowed_tool("zzz", "Read")
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
        return web.json_response({"text": "checklist criada", "is_error": False})

    async def scenario():
        app = web.Application()
        app.router.add_post("/ask", ask)
        app.router.add_post("/tool/update_checklist", checklist)
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
    assert {t["name"] for t in r[2]["result"]["tools"]} >= {
        "ask",
        "update_checklist",
        "ask_user",
        "send_file",
        "schedule",
        "unschedule",
    }
    decision = json.loads(r[3]["result"]["content"][0]["text"])
    assert decision == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert r[4]["result"]["content"][0]["text"] == "checklist criada"
    assert r[5]["result"] == {}
    # os dois tools/call rodam em threads na bridge: a ordem de chegada no HTTP varia
    ask_payload = next(p for p in received if "tool_name" in p)
    checklist_payload = next(p for p in received if "items" in p)
    assert ask_payload["turn"] == "t1" and ask_payload["tool_name"] == "Bash"
    assert checklist_payload["items"] == [{"text": "a"}]


# ---- delivery / media / scheduler / sessions ----


def test_delivery_media_regex():
    from delivery import SENDABLE_RE

    text = "gerei /tmp/out/grafico.png e o relatório em /data/x/rel.pdf; fonte: /src/app.py e `/tmp/a b.jpg`"
    assert SENDABLE_RE.findall(text) == ["/tmp/out/grafico.png", "/data/x/rel.pdf"]


def test_reply_context_pure():
    from media import with_reply_context

    assert with_reply_context("", "oi") == "oi"
    assert with_reply_context("BLOCO", "oi").startswith("BLOCO\n\nMensagem NOVA")


def test_scheduler_validate_and_describe():
    from scheduler import build_trigger, describe, validate

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
    from sessions_index import encode_cwd

    assert encode_cwd("/data/projects") == "-data-projects"
    assert Conversation(1, 2, "x").key == "1:2"
    assert Conversation(1, 2, "x", job_id="7").key == "1:2:job7"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"\n{len(tests)} testes passaram")
