"""Conversas persistidas e registry de projetos."""

from __future__ import annotations

import json
import os
import tempfile
import time

from helpers import CHAT, THREAD

from tgclaude.core.projects import ProjectRegistry
from tgclaude.core.store import Conversation, ConversationStore


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
