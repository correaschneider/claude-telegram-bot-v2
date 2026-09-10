"""Servidor MCP (stdio) que o Claude Code sobe por turno. Só stdlib: cada tools/call vira
um POST no bot (TG_BRIDGE_URL) e bloqueia até o bot responder — é assim que a aprovação
por botão no Telegram chega ao `--permission-prompt-tool`. Nunca importa o resto do bot."""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

URL = os.environ["TG_BRIDGE_URL"].rstrip("/")
TOKEN = os.environ["TG_BRIDGE_TOKEN"]
TURN = os.environ["TG_TURN"]

TOOLS = [
    {
        "name": "ask",
        "description": "Permission prompt: forwards a tool permission request to the Telegram user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
            "required": ["tool_name", "input"],
        },
    },
    {
        "name": "update_checklist",
        "description": (
            "Mostra/atualiza a checklist de etapas desta tarefa no Telegram. Chame ao planejar "
            "(todas as etapas) e sempre que concluir uma (reenvie a lista inteira com done=true)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}},
                        "required": ["text"],
                    },
                },
            },
            "required": ["items"],
        },
    },
]

_out_lock = threading.Lock()


def _write(msg: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{URL}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Bridge-Token": TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=None) as resp:  # noqa: S310 - loopback
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"bridge HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"bridge indisponível: {e}"}


def _call_tool(name: str, args: dict) -> dict:
    if name == "ask":
        decision = _post("ask", {"turn": TURN, **args})
        if "error" in decision:
            decision = {"behavior": "deny", "message": decision["error"]}
        return {"content": [{"type": "text", "text": json.dumps(decision)}]}
    if name == "update_checklist":
        res = _post("checklist", {"turn": TURN, **args})
        return {
            "content": [{"type": "text", "text": res.get("status", json.dumps(res))}],
            "isError": "error" in res,
        }
    return {"content": [{"type": "text", "text": f"tool desconhecida: {name}"}], "isError": True}


def _handle(msg: dict) -> None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        _write(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tg", "version": "1.0"},
                },
            }
        )
    elif method == "tools/list":
        _write({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {})
        _write({"jsonrpc": "2.0", "id": mid, "result": result})
    elif method == "ping":
        _write({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif mid is not None:  # request desconhecido (notificações não recebem resposta)
        _write(
            {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"método {method}"}}
        )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        # tools/call de `ask` bloqueia por minutos; thread por request mantém ping/list vivos.
        threading.Thread(target=_handle, args=(msg,), daemon=True).start()


if __name__ == "__main__":
    main()
