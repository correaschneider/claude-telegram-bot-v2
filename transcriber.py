"""Transcrição de áudio via WhisperX (CLI). Serializada por `flock` quando disponível,
pra duas transcrições não estourarem a VRAM ao mesmo tempo (o lock é compartilhado com
outras pipelines na máquina do autor). Sem `flock` (macOS) roda direto."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from config import Config


class WhisperXTranscriber:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    def _flock_prefix(self) -> list[str]:
        if self._cfg.whisperx_lock and shutil.which("flock"):
            return ["flock", "-x", self._cfg.whisperx_lock]
        return []

    async def transcribe(self, audio_path: str) -> str:
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
