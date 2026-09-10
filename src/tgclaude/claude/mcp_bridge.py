"""Servidor MCP (stdio) que o Claude Code sobe por turno. Só stdlib: cada tools/call vira
um POST no bot (TG_BRIDGE_URL) e bloqueia até o bot responder — é assim que aprovação,
perguntas com botões, checklist, entrega de arquivo e agendamento chegam ao Telegram."""

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


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOLS = [
    {
        "name": "ask",
        "description": "Permission prompt: forwards a tool permission request to the Telegram user.",
        "inputSchema": _obj(
            {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
            ["tool_name", "input"],
        ),
    },
    {
        "name": "update_checklist",
        "description": (
            "Mostra/atualiza a checklist de etapas desta tarefa no Telegram. Chame ao planejar "
            "(todas as etapas) e sempre que concluir uma (reenvie a lista inteira com done=true)."
        ),
        "inputSchema": _obj(
            {
                "title": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": _obj(
                        {"text": {"type": "string"}, "done": {"type": "boolean"}}, ["text"]
                    ),
                },
            },
            ["items"],
        ),
    },
    {
        "name": "ask_user",
        "description": (
            "Faz uma pergunta ao usuário com opções em botões e espera a resposta. "
            "Use quando houver alternativas claras. Retorna o texto da(s) opção(ões) escolhida(s)."
        ),
        "inputSchema": _obj(
            {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "multi": {"type": "boolean", "description": "permite escolher várias"},
            },
            ["question", "options"],
        ),
    },
    {
        "name": "send_file",
        "description": (
            "Envia arquivos do disco ao usuário no Telegram (caminhos absolutos). "
            "Mídia citada na resposta já vai sozinha; use para .md/.json/.txt/.csv etc."
        ),
        "inputSchema": _obj({"paths": {"type": "array", "items": {"type": "string"}}}, ["paths"]),
    },
    {
        "name": "schedule",
        "description": (
            "Agenda um prompt recorrente neste chat. Use `cron` (5 campos) para horários de "
            "calendário ou `every_seconds` (>=5) para intervalos curtos. Retorna o id do job."
        ),
        "inputSchema": _obj(
            {
                "prompt": {"type": "string", "description": "o que executar a cada disparo"},
                "cron": {"type": "string", "description": "ex.: '0 9 * * 1-5'"},
                "every_seconds": {"type": "integer", "minimum": 5},
                "tz": {"type": "string", "description": "IANA, ex.: America/Sao_Paulo"},
                "title": {"type": "string"},
            },
            ["prompt"],
        ),
    },
    {
        "name": "unschedule",
        "description": "Remove um agendamento pelo id (ver /jobs).",
        "inputSchema": _obj({"id": {"type": "string"}}, ["id"]),
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
    if name in {t["name"] for t in TOOLS}:
        res = _post(f"tool/{name}", {"turn": TURN, **args})
        if "error" in res:
            return {"content": [{"type": "text", "text": res["error"]}], "isError": True}
        return {
            "content": [{"type": "text", "text": str(res.get("text", ""))}],
            "isError": bool(res.get("is_error")),
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
                    "serverInfo": {"name": "tg", "version": "1.1"},
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
        # tools/call pode bloquear por minutos; thread por request mantém ping/list vivos.
        threading.Thread(target=_handle, args=(msg,), daemon=True).start()


if __name__ == "__main__":
    main()
