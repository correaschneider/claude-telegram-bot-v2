"""Mesa de permissões: transforma um pedido do `--permission-prompt-tool` numa mensagem
com botões no Telegram e espera a decisão humana (minutos, horas — o processo do Claude
fica segurado pelo MCP_TOOL_TIMEOUT)."""

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

from permissions import decide, describe, is_dangerous, rule_for
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
    def __init__(self, bot: Bot, store: ConversationStore) -> None:
        self._bot = bot
        self._store = store
        self.pending: dict[str, Pending] = {}

    async def ask(self, active: ActiveTurn, tool_name: str, tool_input: dict) -> dict:
        conv = active.conv
        verdict = decide(
            active.rules,
            conv.allow + active.session_allow,
            tool_name,
            tool_input,
            can_prompt=active.can_prompt,
        )
        if verdict == "allow":
            return {"behavior": "allow", "updatedInput": tool_input}
        if verdict == "deny":
            log.info(
                "negado sem prompt (chat sem aprovador): %s %s",
                tool_name,
                describe(tool_name, tool_input)[:80],
            )
            return {
                "behavior": "deny",
                "message": "Ação fora da allowlist e este chat não tem quem aprove. Não tente de novo.",
            }

        rid = secrets.token_hex(5)
        detail = describe(tool_name, tool_input)
        dangerous = is_dangerous(tool_name, tool_input)
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

        if decision == "always":
            rule = rule_for(tool_name, tool_input)
            active.session_allow.append(rule)
            if rule not in conv.allow:
                conv.allow.append(rule)
                self._store.put(conv)
        label = {
            "allow": "✅ Aprovado",
            "always": f"🔁 Aprovado — sempre nesta sessão ({rule_for(tool_name, tool_input)})",
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
