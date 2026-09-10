"""Persistência por conversa = (chat_id, topic_id): projeto, sessão do Claude (com TTL),
regras de permissão aprovadas "sempre nesta sessão", yolo com prazo, mensagem da checklist."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field


@dataclass
class Conversation:
    chat_id: int
    topic_id: int  # 0 = sem tópico (chat geral)
    project: str
    session_id: str | None = None
    updated_at: float = 0.0
    fork_from: str | None = None  # sessão-mãe: 1º turno roda com --fork-session
    allow: list[str] = field(default_factory=list)
    yolo_until: float = 0.0
    checklist_msg: int | None = None
    auto_title: bool = False  # tópico criado pelo bot só com o alias → renomeia após o 1º turno
    job_id: str | None = None  # conversa efêmera de um agendamento (sessão própria, não persiste)

    @property
    def key(self) -> str:
        base = f"{self.chat_id}:{self.topic_id}"
        return f"{base}:job{self.job_id}" if self.job_id else base

    def yolo(self, now: float | None = None) -> bool:
        return self.yolo_until > (now or time.time())


class ConversationStore:
    def __init__(self, path: str, ttl_seconds: int) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._data: dict[str, Conversation] = self._load()

    def _load(self) -> dict[str, Conversation]:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        out = {}
        for key, entry in raw.items():
            try:
                out[key] = Conversation(**entry)
            except TypeError:
                continue
        return out

    def save(self) -> None:
        d = os.path.dirname(self._path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".store-", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in self._data.items()}, f, indent=2)
        os.replace(tmp, self._path)

    def get(self, chat_id: int, topic_id: int) -> Conversation | None:
        conv = self._data.get(f"{chat_id}:{topic_id}")
        if conv and conv.session_id and time.time() - conv.updated_at > self._ttl:
            conv.session_id = None  # sessão expirou; projeto e regras ficam
        return conv

    def ensure(self, chat_id: int, topic_id: int, project: str) -> Conversation:
        conv = self.get(chat_id, topic_id)
        if conv is None:
            conv = Conversation(chat_id=chat_id, topic_id=topic_id, project=project)
            self._data[conv.key] = conv
            self.save()
        return conv

    def put(self, conv: Conversation) -> None:
        self._data[conv.key] = conv
        self.save()

    def set_session(self, conv: Conversation, session_id: str) -> None:
        conv.session_id = session_id
        conv.fork_from = None
        conv.updated_at = time.time()
        self.put(conv)

    def clear_session(self, conv: Conversation) -> None:
        conv.session_id = None
        conv.fork_from = None
        conv.checklist_msg = None
        self.put(conv)

    def all_for_chat(self, chat_id: int) -> list[Conversation]:
        return [c for c in self._data.values() if c.chat_id == chat_id]
