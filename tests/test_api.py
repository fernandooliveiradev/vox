#!/usr/bin/env python3
"""Testes ponta a ponta do app Vox (sem dependências externas além do venv).

Uso:
    .venv/bin/python tests/test_api.py
"""
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import app, db  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "jfk.flac"

_results = []


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not condition else ""))


def sse_events(text):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if event:
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"raw": data}
            events.append((event, payload))
    return events


def main():
    if not SAMPLE.exists():
        print(f"Amostra de áudio não encontrada: {SAMPLE}")
        return 1

    with TestClient(app) as client:
        # estado limpo: zera trial e licença para o teste ser reprodutível
        with db() as conn:
            conn.execute("DELETE FROM settings WHERE key IN ('trial_used', 'license', 'boas_vindas_vista')")

        # --- páginas e health ---
        r = client.get("/")
        check("GET / responde 200", r.status_code == 200)
        check("página contém o formulário", "Transcrever" in r.text)
        r = client.get("/docs")
        check("GET /docs responde 200", r.status_code == 200)
        check("documentação explica o uso", "Como usar o Vox" in r.text)
        r = client.get("/health")
        check("GET /health", r.status_code == 200 and r.json()["status"] == "ok")
        r = client.get("/favicon.ico")
        check("GET /favicon.ico sem erro", r.status_code in (200, 204), str(r.status_code))
        r = client.get("/api/models")
        check("lista de modelos", r.status_code == 200 and len(r.json()) == 4)

        # --- validações ---
        r = client.post("/api/upload", files={"file": ("nota.txt", b"texto", "text/plain")})
        check("upload com formato inválido é recusado", r.status_code == 400, str(r.status_code))

        r = client.get("/api/transcribe/stream")
        check("stream sem link ou arquivo é recusado", r.status_code == 400)

        r = client.get("/api/transcribe/stream", params={"url": "https://x.com/a", "model": "gigante"})
        check("modelo inválido é recusado", r.status_code == 400)

        r = client.get("/api/transcribe/stream", params={"url": "file:///etc/passwd"})
        check("URL não-http é recusada", r.status_code == 400)

        r = client.get("/api/transcribe/stream", params={"url": "--exec=rm -rf /"})
        check("URL que começa com '-' é recusada", r.status_code == 400)

        r = client.get("/api/transcribe/stream", params={"file_id": "../../etc/passwd"})
        check("file_id malicioso é recusado", r.status_code == 400)

        r = client.post("/api/cancel/" + "x" * 100)
        check("job_id inválido é recusado", r.status_code == 400, str(r.status_code))
        r = client.post("/api/cancel/abc$def")
        check("job_id com caractere inválido é recusado", r.status_code == 400, str(r.status_code))

        # --- transcrição de arquivo local ---
        with SAMPLE.open("rb") as fh:
            r = client.post("/api/upload", files={"file": ("jfk.flac", fh, "audio/flac")})
        check("upload válido", r.status_code == 200, r.text[:200])
        file_id = r.json()["file_id"]

        r = client.get("/api/transcribe/stream", params={"file_id": file_id, "model": "tiny"})
        events = sse_events(r.text)
        done = [p for e, p in events if e == "done"]
        errors = [p for e, p in events if e == "error"]
        check("transcrição concluída (evento done)", bool(done), r.text[:300])
        check("transcrição sem erro", not errors, str(errors)[:200])
        if done:
            check("texto transcrito não vazio", len(done[0]["text"]) > 20)
            check("idioma detectado", bool(done[0].get("language")))
            item_id = done[0]["id"]
        else:
            item_id = None

        # --- histórico ---
        r = client.get("/api/history")
        check("histórico lista itens", r.status_code == 200 and isinstance(r.json(), list))
        if item_id:
            check("transcrição aparece no histórico", any(x["id"] == item_id for x in r.json()))
            r = client.get(f"/api/history/{item_id}")
            check("detalhe da transcrição", r.status_code == 200 and len(r.json()["text"]) > 20)
            check("segmentos salvos", len(r.json()["segments_json"]) > 0)

            r = client.get(f"/api/history/{item_id}/export", params={"fmt": "txt"})
            check("export TXT", r.status_code == 200 and "[" in r.text)
            r = client.get(f"/api/history/{item_id}/export", params={"fmt": "srt"})
            check("export SRT", r.status_code == 200 and "-->" in r.text)
            r = client.get(f"/api/history/{item_id}/export", params={"fmt": "vtt"})
            check("export VTT", r.status_code == 200 and r.text.startswith("WEBVTT"))
            r = client.get(f"/api/history/{item_id}/export", params={"fmt": "txt"})
            check("export tem nome de arquivo", "filename=" in r.headers.get("content-disposition", ""))

            r = client.delete(f"/api/history/{item_id}")
            check("exclusão funciona", r.status_code == 200 and r.json()["deleted"] is True)
            r = client.get(f"/api/history/{item_id}")
            check("item excluído não existe mais", r.status_code == 404)

        r = client.get("/api/history/999999")
        check("histórico inexistente devolve 404", r.status_code == 404)

        # --- tradução ---
        with SAMPLE.open("rb") as fh:
            r = client.post("/api/upload", files={"file": ("jfk.flac", fh, "audio/flac")})
        file_id = r.json()["file_id"]
        r = client.get("/api/transcribe/stream",
                       params={"file_id": file_id, "model": "tiny", "translate": "true"})
        events = sse_events(r.text)
        done = [p for e, p in events if e == "done"]
        check("tradução concluída", bool(done), r.text[:300])
        if done:
            text = done[0]["text"]
            check("texto traduzido está em português",
                  any(w in text.lower() for w in ("portugu", "país", "patriot", "obrigado", "e assim")))
            client.delete(f"/api/history/{done[0]['id']}")

        # --- ferramentas embutidas (yt-dlp/ffmpeg) ---
        import pathlib
        import tempfile

        import app as app_mod

        fake = pathlib.Path(tempfile.mkdtemp()) / "Vox"
        (fake / "bin").mkdir(parents=True)
        for name in ("yt-dlp", "ffmpeg"):
            tool = fake / "bin" / name
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
        old_dirs, old_frozen = app_mod.tool_dirs, app_mod.FROZEN
        old_exe = app_mod.sys.executable
        try:
            app_mod.FROZEN = True
            app_mod.sys.executable = str(fake / "Vox")
            app_mod.tool_dirs = lambda: [fake / "bin", fake]
            app_mod.setup_bundled_tools()
            check("yt-dlp embutido tem prioridade", app_mod.yt_dlp_command()[0].startswith(str(fake)),
                  str(app_mod.yt_dlp_command()))
            check("ffmpeg embutido encontrado", (app_mod.ffmpeg_path() or "").startswith(str(fake)),
                  str(app_mod.ffmpeg_path()))
        finally:
            app_mod.tool_dirs, app_mod.FROZEN = old_dirs, old_frozen
            app_mod.sys.executable = old_exe

        # --- licença ---
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        import license as license_mod

        r = client.get("/api/license")
        check("GET /api/license responde", r.status_code == 200 and "status" in r.json())

        r = client.post("/api/license/activate", json={"key": "VOX.CHAVE.FALSA.123"})
        check("chave inválida é recusada", r.status_code == 400, str(r.status_code))

        private = Ed25519PrivateKey.generate()
        pub_hex = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()
        license_mod.PUBLIC_KEY_HEX = pub_hex
        good_key = license_mod.make_key(private, tier="pro")

        r = client.post("/api/license/activate", json={"key": good_key})
        check("chave válida é aceita", r.status_code == 200 and r.json()["status"] == "ativa",
              r.text[:200])
        r = client.get("/api/license")
        check("licença ativa é reconhecida", r.json()["status"] == "ativa", r.text[:200])

        with SAMPLE.open("rb") as fh:
            r = client.post("/api/upload", files={"file": ("jfk.flac", fh, "audio/flac")})
        fid = r.json()["file_id"]
        r = client.get("/api/transcribe/stream", params={"file_id": fid, "model": "tiny"})
        events = sse_events(r.text)
        check("transcrição funciona com licença ativa",
              any(e == "done" for e, _ in events), r.text[-200:])

        # limpa a licença do banco de testes
        with db() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'license'")
        license_mod.PUBLIC_KEY_HEX = os.environ.get(
            "VOX_LICENSE_PUBLIC_KEY",
            "14561f63406bd0912e6c422f9f0abeb58b75a6cd2ee6c0784f2f3668519c8cb7",
        )

        # --- API compatível com OpenAI ---
        r = client.get("/v1/models")
        check("GET /v1/models", r.status_code == 200 and r.json()["object"] == "list")
        with SAMPLE.open("rb") as fh:
            r = client.post("/v1/audio/transcriptions", files={"file": ("jfk.flac", fh, "audio/flac")})
        check("POST /v1/audio/transcriptions", r.status_code == 200 and len(r.json()["text"]) > 10)

        # --- proteção de origem (CSRF) ---
        r = client.post("/api/upload", files={"file": ("jfk.flac", SAMPLE.open("rb"), "audio/flac")},
                        headers={"Origin": "https://site-malicioso.example"})
        check("requisição de outra origem é bloqueada", r.status_code == 403, str(r.status_code))

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} testes passaram")
    if passed != total:
        print("\nFalhas:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
