"""Digest de vídeo: funções puras (timestamps, transcrição, validação dos momentos, prompt)."""

import json
import os
import tempfile

from tgclaude.tools.video import (
    Moment,
    digest_prompt,
    fmt_ts,
    parse_moments,
    transcript_lines,
    write_transcripts,
)


def test_fmt_ts_and_transcript_lines():
    assert fmt_ts(0) == "00:00" and fmt_ts(125.4) == "02:05" and fmt_ts(3725) == "1:02:05"
    segs = [
        {"start": 0.6, "text": " Oi "},
        {"start": 61.2, "text": ""},
        {"start": 90, "text": "fim"},
    ]
    assert transcript_lines(segs) == "[00:00] Oi\n[01:30] fim"


def test_parse_moments_validates():
    payload = {
        "moments": [
            {"t": 12.5, "label": "abre a tela", "quote": "olha aqui"},
            {"t": 12.9, "label": "duplicado no mesmo segundo", "quote": "x"},
            {"t": "abc", "label": "inválido", "quote": ""},
            {"t": 999, "label": "fora do vídeo", "quote": ""},
            {"t": 3, "label": "l" * 100, "quote": "antes"},
        ]
    }
    ms = parse_moments(payload, duration=100)
    assert [m.t for m in ms] == [3.0, 12.5]
    assert len(ms[0].label) == 60
    assert parse_moments(None, 10) == [] and parse_moments({"moments": "nope"}, 10) == []


def test_write_transcripts_and_prompt():
    with tempfile.TemporaryDirectory() as d:
        video = os.path.join(d, "call.mp4")
        txt, js = write_transcripts(video, "oi tudo", [{"start": 0, "end": 1, "text": "oi tudo"}])
        with open(txt, encoding="utf-8") as f:
            assert txt.endswith("call.transcript.txt") and f.read() == "oi tudo\n"
        with open(js, encoding="utf-8") as f:
            assert json.load(f)["segments"][0]["text"] == "oi tudo"
        moments = [
            Moment(12.5, "abre a tela", "olha aqui", os.path.join(d, "m1.jpg")),
            Moment(40, "sem print", "x"),
        ]
        p = digest_prompt("", video, 95, txt, moments)
        assert p.startswith("Faça o digest deste vídeo.")
        assert f"[Vídeo enviado pelo usuário: {video} — duração 01:35]" in p
        assert '1. [00:12] abre a tela — "olha aqui" → ' + os.path.join(d, "m1.jpg") in p
        assert "2. [00:40] sem print" in p and "(sem print útil neste instante)" in p
        assert digest_prompt("resume a call", video, 95, txt, moments).startswith("resume a call")
