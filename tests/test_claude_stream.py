"""Parser do stream-json e montagem dos argumentos do CLI."""

from __future__ import annotations

import json

from tgclaude.claude.stream import (
    Init,
    Result,
    RunSpec,
    TextDelta,
    ToolDone,
    ToolStart,
    build_args,
    parse_line,
)


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
