"""Servidor WhisperX residente: carrega o modelo UMA vez e transcreve por HTTP em loopback.

Roda no venv do WhisperX (não no do bot): `~/whisperx/.venv/bin/python whisperx_server.py`.
Só stdlib + whisperx. O modelo é carregado sob demanda e descarregado após
WHISPERX_IDLE_SECONDS sem uso (libera VRAM pra outras pipelines); cada transcrição
segura o mesmo flock que o CLI usa, então não concorre com o whisperx-cli do OBS.

POST /transcribe  {"path": "/abs/audio.ogg", "language": "pt"}  → {"text": "...", "seconds": 0.4}
GET  /health                                                     → {"loaded": bool, "model": ...}
"""

from __future__ import annotations

import fcntl
import gc
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("WHISPERX_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHISPERX_PORT", "8765"))
MODEL = os.environ.get("WHISPERX_MODEL", "large-v3")
DEVICE = os.environ.get("WHISPERX_DEVICE", "cuda")
COMPUTE = os.environ.get("WHISPERX_COMPUTE_TYPE", "float16")
LANGUAGE = os.environ.get("WHISPERX_LANGUAGE", "pt")
BATCH = int(os.environ.get("WHISPERX_BATCH", "8"))
IDLE_SECONDS = int(os.environ.get("WHISPERX_IDLE_SECONDS", "900"))
LOCK_PATH = os.environ.get("WHISPERX_LOCK", "/tmp/whisperx-pipeline.lock")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s whisperx-server: %(message)s"
)
log = logging.getLogger("whisperx-server")


class Engine:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    def _load(self):
        import whisperx

        t = time.time()
        self._model = whisperx.load_model(MODEL, DEVICE, compute_type=COMPUTE, language=LANGUAGE)
        log.info("modelo %s (%s/%s) carregado em %.1fs", MODEL, DEVICE, COMPUTE, time.time() - t)
        return self._model

    def preload(self) -> None:
        with self._lock:
            if self._model is None:
                self._load()
                self._last_used = time.time()

    def unload_if_idle(self) -> None:
        with self._lock:
            if self._model is not None and time.time() - self._last_used > IDLE_SECONDS:
                self._model = None
                gc.collect()
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
                log.info("modelo descarregado por ociosidade (%ds)", IDLE_SECONDS)

    def transcribe(self, path: str, language: str) -> tuple[str, list[dict]]:
        """(texto corrido, segments [{start, end, text}]) — mesmo formato do whisperx CLI."""
        import whisperx

        with self._lock:
            model = self._model or self._load()
            self._last_used = time.time()
            lock_fd = None
            if LOCK_PATH:
                lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o666)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                audio = whisperx.load_audio(path)
                result = model.transcribe(audio, batch_size=BATCH, language=language or LANGUAGE)
            finally:
                if lock_fd is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
            self._last_used = time.time()
            segments = [
                {
                    "start": round(float(s.get("start", 0.0)), 3),
                    "end": round(float(s.get("end", 0.0)), 3),
                    "text": str(s.get("text", "")).strip(),
                }
                for s in result.get("segments", [])
            ]
            return " ".join(s["text"] for s in segments if s["text"]).strip(), segments

    @property
    def loaded(self) -> bool:
        return self._model is not None


engine = Engine()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silencia o access log padrão
        return

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"loaded": engine.loaded, "model": MODEL, "compute": COMPUTE})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/transcribe":
            self._json(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            path = str(payload.get("path") or "")
            if not os.path.isfile(path):
                self._json(400, {"error": f"arquivo não encontrado: {path}"})
                return
            t = time.time()
            text, segments = engine.transcribe(path, str(payload.get("language") or LANGUAGE))
            log.info(
                "transcrito %s em %.2fs (%d chars, %d segments)",
                os.path.basename(path),
                time.time() - t,
                len(text),
                len(segments),
            )
            self._json(
                200, {"text": text, "segments": segments, "seconds": round(time.time() - t, 2)}
            )
        except Exception as e:  # noqa: BLE001
            log.exception("falha na transcrição")
            self._json(500, {"error": str(e)[:500]})


def _idle_loop() -> None:
    while True:
        time.sleep(30)
        engine.unload_if_idle()


def main() -> None:
    threading.Thread(target=_idle_loop, daemon=True).start()
    if os.environ.get("WHISPERX_PRELOAD", "1") == "1":
        threading.Thread(target=engine.preload, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info(
        "ouvindo em http://%s:%d (modelo %s, idle-unload %ds)", HOST, PORT, MODEL, IDLE_SECONDS
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
