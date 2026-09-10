"""Agendamentos recorrentes: o Claude chama a tool MCP `schedule` (cron de 5 campos ou
`every_seconds`), o job fica em `.jobs.json` e o APScheduler dispara um turno no tópico
onde foi criado, com sessão do Claude DEDICADA ao job (não mistura com a conversa)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from store import Conversation

if TYPE_CHECKING:
    from services import Services

log = logging.getLogger("claude-bot")

MIN_EVERY_SECONDS = 5


class JobStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self.jobs: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            raw = {}
        for jid, j in raw.items():
            if (
                isinstance(j, dict)
                and j.get("prompt")
                and (j.get("cron") or j.get("every_seconds"))
            ):
                self.jobs[str(jid)] = j

    def save(self) -> None:
        d = os.path.dirname(self._path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".jobs-", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def next_id(self) -> str:
        n = 0
        for k in self.jobs:
            with contextlib.suppress(ValueError):
                n = max(n, int(k))
        return str(n + 1)


def describe(job: dict) -> str:
    when = f"a cada {job['every_seconds']}s" if job.get("every_seconds") else job.get("cron")
    return f"{when} ({job.get('tz')})"


def build_trigger(job: dict):
    tz = ZoneInfo(job["tz"])
    if job.get("every_seconds"):
        return IntervalTrigger(seconds=int(job["every_seconds"]), timezone=tz)
    return CronTrigger.from_crontab(job["cron"], timezone=tz)


def validate(spec: dict, default_tz: str) -> dict:
    """Normaliza o pedido da tool. Levanta ValueError com mensagem legível."""
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt vazio")
    tz = str(spec.get("tz") or default_tz)
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"timezone desconhecida: {tz}") from e
    job = {"prompt": prompt, "tz": tz, "title": str(spec.get("title") or prompt[:40])}
    every = spec.get("every_seconds")
    cron = str(spec.get("cron") or "").strip()
    if every is not None and str(every) != "":
        every = int(every)
        if every < MIN_EVERY_SECONDS:
            raise ValueError(f"every_seconds mínimo é {MIN_EVERY_SECONDS}")
        job["every_seconds"] = every
    elif cron:
        CronTrigger.from_crontab(cron, timezone=ZoneInfo(tz))  # valida
        job["cron"] = cron
    else:
        raise ValueError("informe cron (5 campos) ou every_seconds")
    return job


class JobScheduler:
    def __init__(self, store: JobStore, default_tz: str) -> None:
        self.store = store
        self.default_tz = default_tz
        self._sched = AsyncIOScheduler()
        self._services: Services | None = None

    def attach(self, services: Services) -> None:
        self._services = services

    def start(self) -> None:
        for jid, job in self.store.jobs.items():
            self._register(jid, job)
        self._sched.start()
        if self.store.jobs:
            log.info("agendamentos restaurados: %d", len(self.store.jobs))

    def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            self._sched.shutdown(wait=False)

    def _register(self, jid: str, job: dict) -> None:
        self._sched.add_job(
            self._fire, build_trigger(job), args=[jid], id=f"job-{jid}", replace_existing=True
        )

    def add(self, conv: Conversation, spec: dict) -> tuple[str, dict]:
        job = validate(spec, self.default_tz)
        job.update(
            chat_id=conv.chat_id,
            topic_id=conv.topic_id,
            project=conv.project,
            session_id=None,
            created_at=time.time(),
        )
        jid = self.store.next_id()
        self.store.jobs[jid] = job
        self.store.save()
        self._register(jid, job)
        return jid, job

    def remove(self, jid: str) -> bool:
        if jid not in self.store.jobs:
            return False
        self.store.jobs.pop(jid)
        self.store.save()
        with contextlib.suppress(Exception):
            self._sched.remove_job(f"job-{jid}")
        return True

    def for_chat(self, chat_id: int) -> list[tuple[str, dict]]:
        return [(jid, j) for jid, j in self.store.jobs.items() if j.get("chat_id") == chat_id]

    async def _fire(self, jid: str) -> None:
        from runner import is_busy, run_turn  # import tardio: runner importa services
        from telegram_sink import TelegramSink

        services = self._services
        job = self.store.jobs.get(jid)
        if services is None or job is None:
            return
        conv = Conversation(
            chat_id=job["chat_id"],
            topic_id=job.get("topic_id") or 0,
            project=job.get("project") or services.projects.default.alias,
            session_id=job.get("session_id"),
            job_id=jid,
        )
        if is_busy(services, conv):
            log.info("job %s pulado: disparo anterior ainda rodando", jid)
            return
        log.info("job %s disparando: %s", jid, job.get("title"))
        try:
            await services.bot.send_message(
                conv.chat_id,
                f"⏰ #{jid} {job.get('title')}",
                message_thread_id=conv.topic_id or None,
            )
            outcome = await run_turn(
                services,
                conv,
                job["prompt"],
                draft_id=int(time.time()),
                sink=TelegramSink(services.bot, use_rich=services.cfg.rich_messages),
                actor_id=conv.chat_id,
                allow_prompt=conv.chat_id > 0,
            )
        except Exception:
            log.exception("job %s falhou", jid)
            return
        if outcome.session_id and outcome.session_id != job.get("session_id"):
            job["session_id"] = outcome.session_id
            job["last_run"] = time.time()
            self.store.save()
