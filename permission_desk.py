"""Mesa de permissões: aplica a política (read-only / projeto / sensível / "sempre"), registra
cada decisão no log de auditoria e, quando precisa de humano, transforma o pedido do
`--permission-prompt-tool` numa mensagem com botões e espera (o processo do Claude fica
segurado pelo MCP_TOOL_TIMEOUT)."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import secrets
import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

import audit
from permissions import PolicyLoader, describe, rule_for
from services import ActiveTurn
from store import ConversationStore

log = logging.getLogger("claude-bot")

CONFIRM_WINDOW = 20.0


@dataclass
class Pending:
    active: ActiveTurn
    tool_name: str
    tool_input: dict
    message_id: int
    dangerous: bool
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())
    confirm_at: float = 0.0


def _keyboard(rid: str, dangerous: bool, copy_text: str | None) -> InlineKeyboardMarkup:
    approve = "⚠️ Aprovar (sensível)" if dangerous else "✅ Aprovar"
    rows = [
        [
            InlineKeyboardButton(text=approve, callback_data=f"perm:{rid}:a"),
            InlineKeyboardButton(text="❌ Negar", callback_data=f"perm:{rid}:d"),
        ],
        [InlineKeyboardButton(text="🔁 Sempre nesta sessão", callback_data=f"perm:{rid}:s")],
    ]
    if copy_text:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📋 Copiar comando", copy_text=CopyTextButton(text=copy_text)
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class PermissionDesk:
    def __init__(
        self, bot: Bot, store: ConversationStore, policy: PolicyLoader, decisions_file: str
    ) -> None:
        self._bot = bot
        self._store = store
        self._policy = policy
        self._decisions = decisions_file
        self.pending: dict[str, Pending] = {}

    def _record(self, active: ActiveTurn, tool_name: str, tool_input: dict, **kw) -> None:
        audit.record(
            self._decisions,
            chat_id=active.conv.chat_id,
            project=active.conv.project,
            tool=tool_name,
            detail=describe(tool_name, tool_input),
            rule=rule_for(tool_name, tool_input),
            **kw,
        )

    async def ask(self, active: ActiveTurn, tool_name: str, tool_input: dict) -> dict:
        conv = active.conv
        policy = self._policy.get()
        dangerous = policy.is_dangerous(tool_name, tool_input)
        verdict, matched = policy.decide(
            active.rules,
            conv.allow + active.session_allow,
            tool_name,
            tool_input,
            can_prompt=active.can_prompt,
        )
        if verdict == "allow":
            self._record(
                active,
                tool_name,
                tool_input,
                verdict="allow",
                outcome="auto",
                matched=matched,
                dangerous=dangerous,
            )
            return {"behavior": "allow", "updatedInput": tool_input}
        if verdict == "deny":
            log.info(
                "negado sem prompt (chat sem aprovador): %s %s",
                tool_name,
                describe(tool_name, tool_input)[:80],
            )
            self._record(
                active,
                tool_name,
                tool_input,
                verdict="deny",
                outcome="denied-no-prompter",
                matched=None,
                dangerous=dangerous,
            )
            return {
                "behavior": "deny",
                "message": "Ação fora da allowlist e este chat não tem quem aprove. Não tente de novo.",
            }

        rid = secrets.token_hex(5)
        detail = describe(tool_name, tool_input)
        text = (
            f"🔐 <b>Permissão</b> · <code>{html.escape(tool_name)}</code>"
            + (" · ⚠️ sensível" if dangerous else "")
            + f"\n<pre>{html.escape(detail[:3000])}</pre>"
        )
        copy_text = detail[:256] if tool_name == "Bash" else None
        msg = await self._bot.send_message(
            conv.chat_id,
            text,
            parse_mode="HTML",
            message_thread_id=conv.topic_id or None,
            reply_markup=_keyboard(rid, dangerous, copy_text),
        )
        pending = Pending(active, tool_name, tool_input, msg.message_id, dangerous)
        self.pending[rid] = pending
        active.turn.set_status(f"🔐 aguardando sua aprovação · {tool_name}")
        try:
            decision: str = await pending.future
        except asyncio.CancelledError:
            decision = "cancel"
        finally:
            self.pending.pop(rid, None)
            active.turn.set_status("")

        rule = rule_for(tool_name, tool_input)
        if decision == "always":
            active.session_allow.append(rule)
            if rule not in conv.allow:
                conv.allow.append(rule)
                self._store.put(conv)
        self._record(
            active,
            tool_name,
            tool_input,
            verdict="prompt",
            outcome=decision,
            matched=None,
            dangerous=dangerous,
        )
        label = {
            "allow": "✅ Aprovado",
            "always": f"🔁 Aprovado — sempre nesta sessão ({rule})",
            "deny": "❌ Negado",
            "cancel": "⏹ Cancelado",
        }[decision]
        with contextlib.suppress(TelegramBadRequest):
            await self._bot.edit_message_text(
                f"{text}\n{label}",
                chat_id=conv.chat_id,
                message_id=msg.message_id,
                parse_mode="HTML",
            )
        if decision in ("allow", "always"):
            return {"behavior": "allow", "updatedInput": tool_input}
        return {"behavior": "deny", "message": "O usuário negou esta ação pelo Telegram."}

    def resolve(self, rid: str, action: str) -> str | None:
        """Chamado pelo callback do botão. Devolve mensagem de feedback, ou None se expirou.
        Comando sensível exige segundo toque em até CONFIRM_WINDOW segundos."""
        p = self.pending.get(rid)
        if p is None or p.future.done():
            return None
        if action in ("a", "s") and p.dangerous:
            now = time.monotonic()
            if now - p.confirm_at > CONFIRM_WINDOW:
                p.confirm_at = now
                return "confirm"
        p.future.set_result({"a": "allow", "s": "always", "d": "deny"}[action])
        return "ok"

    def cancel_all(self, active: ActiveTurn) -> None:
        for p in list(self.pending.values()):
            if p.active is active and not p.future.done():
                p.future.cancel()
