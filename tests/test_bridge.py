"""Protocolo MCP stdio: sobe o mcp_bridge.py real contra um HTTP falso."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from aiohttp import web
from helpers import BRIDGE


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
            BRIDGE,
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
