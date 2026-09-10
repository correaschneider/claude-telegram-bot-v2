"""Lado do bot da bridge MCP: HTTP em loopback que o `mcp_bridge.py` (spawnado pelo Claude)
chama. `/ask` bloqueia até a decisão humana; `/tool/<nome>` despacha as demais tools."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from aiohttp import web

from checklist import update_checklist
from delivery import send_paths
from scheduler import describe
from services import ActiveTurn, Services

log = logging.getLogger("claude-bot")

ToolHandler = Callable[[Services, ActiveTurn, dict], Awaitable[tuple[str, bool]]]


async def _t_update_checklist(
    services: Services, active: ActiveTurn, args: dict
) -> tuple[str, bool]:
    items = args.get("items") or []
    if not isinstance(items, list):
        return "items deve ser uma lista", True
    status = await update_checklist(services, active.conv, str(args.get("title") or ""), items)
    return status, False


async def _t_ask_user(services: Services, active: ActiveTurn, args: dict) -> tuple[str, bool]:
    options = args.get("options") or []
    if not isinstance(options, list):
        return "options deve ser uma lista", True
    answer = await services.questions.ask(
        active, str(args.get("question") or "?"), options, bool(args.get("multi"))
    )
    return answer, False


async def _t_send_file(services: Services, active: ActiveTurn, args: dict) -> tuple[str, bool]:
    paths = [str(p) for p in (args.get("paths") or []) if str(p).strip()]
    if not paths:
        return "nenhum caminho informado", True
    before = len(active.sent_files)
    await send_paths(
        services.bot,
        active.conv.chat_id,
        active.conv.topic_id or None,
        paths,
        sent=active.sent_files,
        warn_missing=True,
    )
    n = len(active.sent_files) - before
    return f"{n} arquivo(s) enviado(s)", n == 0


async def _t_schedule(services: Services, active: ActiveTurn, args: dict) -> tuple[str, bool]:
    try:
        jid, job = services.scheduler.add(active.conv, args)
    except ValueError as e:
        return f"agendamento inválido: {e}", True
    return f"⏰ #{jid} agendado: {describe(job)} — {job['title']}", False


async def _t_unschedule(services: Services, active: ActiveTurn, args: dict) -> tuple[str, bool]:
    jid = str(args.get("id") or "").lstrip("#")
    if services.scheduler.remove(jid):
        return f"agendamento #{jid} removido", False
    return f"agendamento #{jid} não existe", True


TOOLS: dict[str, ToolHandler] = {
    "update_checklist": _t_update_checklist,
    "ask_user": _t_ask_user,
    "send_file": _t_send_file,
    "schedule": _t_schedule,
    "unschedule": _t_unschedule,
}


class BridgeServer:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self.token = secrets.token_urlsafe(24)
        self.url = ""
        self._services: Services | None = None
        self._runner: web.AppRunner | None = None

    def attach(self, services: Services) -> None:
        self._services = services

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/ask", self._ask)
        app.router.add_post("/tool/{name}", self._tool)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.url = f"http://{self._host}:{port}"
        log.info("bridge MCP ouvindo em %s", self.url)
        return self.url

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _authorized(self, request: web.Request) -> tuple[dict, ActiveTurn]:
        assert self._services is not None
        if request.headers.get("X-Bridge-Token") != self.token:
            raise web.HTTPForbidden(text="token inválido")
        try:
            payload = await request.json()
        except ValueError as e:
            raise web.HTTPBadRequest(text="json inválido") from e
        active = self._services.state.by_token.get(str(payload.get("turn", "")))
        if active is None:
            raise web.HTTPNotFound(text="turno não está ativo")
        return payload, active

    async def _ask(self, request: web.Request) -> web.Response:
        payload, active = await self._authorized(request)
        assert self._services is not None
        tool_name = str(payload.get("tool_name", "?"))
        tool_input = payload.get("input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {"value": tool_input}
        decision = await self._services.desk.ask(active, tool_name, tool_input)
        return web.json_response(decision)

    async def _tool(self, request: web.Request) -> web.Response:
        payload, active = await self._authorized(request)
        assert self._services is not None
        handler = TOOLS.get(request.match_info["name"])
        if handler is None:
            return web.json_response({"text": "tool desconhecida", "is_error": True}, status=404)
        try:
            text, is_error = await handler(self._services, active, payload)
        except Exception as e:  # noqa: BLE001 — erro da tool não pode derrubar o turno
            log.exception("tool %s falhou", request.match_info["name"])
            text, is_error = f"erro interno na tool: {e}", True
        return web.json_response({"text": text, "is_error": is_error})
