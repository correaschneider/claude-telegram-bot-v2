"""Transcrição de áudio via WhisperX.

Caminho rápido: servidor residente (`deploy/whisperx_server.py`) que já tem o modelo em
memória — ~0,5 s por áudio. Fallback: CLI `whisperx` do zero (~10 s, sobe Python+CUDA+modelo
a cada chamada), serializado por `flock` pra não disputar VRAM com outras pipelines."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path

import aiohttp

from tgclaude.config import Config

log = logging.getLogger("claude-bot")


class WhisperXTranscriber:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    async def transcribe(self, audio_path: str) -> str:
        if self._cfg.whisperx_server_url:
            try:
                return await self._via_server(audio_path)
            except (aiohttp.ClientConnectionError, TimeoutError) as e:
                # Só cai pro CLI se o servidor estiver FORA: com ele no ar a VRAM está
                # ocupada e um segundo large-v3 daria CUDA out of memory.
                log.warning("whisperx-server inalcançável (%s); usando CLI", str(e)[:200])
        return await self._via_cli(audio_path)

    async def _via_server(self, audio_path: str) -> str:
        c = self._cfg
        timeout = aiohttp.ClientTimeout(total=600, connect=3)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                f"{c.whisperx_server_url.rstrip('/')}/transcribe",
                json={"path": audio_path, "language": c.whisperx_language},
            ) as resp,
        ):
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(body.get("error") or f"HTTP {resp.status}")
            log.info("whisperx-server: %.2fs", body.get("seconds", 0))
            return str(body.get("text", "")).strip()

    def _flock_prefix(self) -> list[str]:
        if self._cfg.whisperx_lock and shutil.which("flock"):
            return ["flock", "-x", self._cfg.whisperx_lock]
        return []

    async def _via_cli(self, audio_path: str) -> str:
        c = self._cfg
        Path(c.audio_tmp_dir).mkdir(parents=True, exist_ok=True)
        out_dir = tempfile.mkdtemp(prefix="whisperx_", dir=c.audio_tmp_dir)
        try:
            cmd = [
                *self._flock_prefix(),
                c.whisperx_bin, audio_path,
                "--model", c.whisperx_model,
                "--device", c.whisperx_device,
                "--compute_type", c.whisperx_compute_type,
                "--language", c.whisperx_language,
                "--batch_size", c.whisperx_batch,
                "--no_align",  # só o texto interessa; poupa o modelo de alinhamento
                "--output_format", "json",
                "--output_dir", out_dir,
            ]  # fmt: skip
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"whisperx exit {proc.returncode}: {stderr.decode('utf-8', 'replace')[:500]}"
                )
            json_files = list(Path(out_dir).glob("*.json"))
            if not json_files:
                raise RuntimeError("whisperx não produziu JSON")
            with open(json_files[0], encoding="utf-8") as f:
                result = json.load(f)
            return " ".join(s.get("text", "").strip() for s in result.get("segments", [])).strip()
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
