"""Lado do bot da bridge MCP: HTTP em loopback que o `mcp_bridge.py` (spawnado pelo Claude)
chama. `/ask` bloqueia até a decisão humana; `/checklist` atualiza a checklist do tópico."""

from __future__ import annotations

import logging
import secrets

from aiohttp import web

from checklist import update_checklist
from services import Services

log = logging.getLogger("claude-bot")


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
        app.router.add_post("/checklist", self._checklist)
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

    async def _authorized(self, request: web.Request):
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

    async def _checklist(self, request: web.Request) -> web.Response:
        payload, active = await self._authorized(request)
        assert self._services is not None
        items = payload.get("items") or []
        if not isinstance(items, list):
            return web.json_response({"error": "items deve ser lista"}, status=400)
        status = await update_checklist(
            self._services, active.conv, str(payload.get("title") or ""), items
        )
        return web.json_response({"status": status})
