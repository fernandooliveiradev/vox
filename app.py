"""Vox — webapp local de transcrição (YouTube, arquivos locais) via faster-whisper."""
import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from faster_whisper import WhisperModel

import license

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_data_dir() -> Path:
    """Pasta de dados do usuário (gravável) — funciona empacotado ou em dev."""
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Vox"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Vox"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "vox"


DATA_DIR = Path(os.getenv("VOX_DATA_DIR") or (user_data_dir() if FROZEN else BASE_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "vox.db"
for d in (DATA_DIR, CACHE_DIR, UPLOAD_DIR):
    d.mkdir(parents=True, exist_ok=True)


def tool_dirs() -> list:
    """Pastas onde procuramos ffmpeg/yt-dlp empacotados.

    No build empacotado (PyInstaller onedir) os binarios ficam em
    <app>/bin, ao lado de _internal — por isso procuramos nos dois lugares.
    """
    dirs = []
    if FROZEN:
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        dirs += [exe_dir / "bin", exe_dir, meipass / "bin", meipass]
    else:
        dirs += [BASE_DIR / "bin", BASE_DIR]
    seen, out = set(), []
    for d in dirs:
        try:
            if d.is_dir() and str(d) not in seen:
                seen.add(str(d))
                out.append(d)
        except OSError:
            pass
    return out


def setup_bundled_tools():
    """Coloca ffmpeg/yt-dlp empacotados no PATH (builds de desktop)."""
    current = os.environ.get("PATH", "")
    parts = [str(d) for d in tool_dirs() if str(d) not in current.split(os.pathsep)]
    if parts:
        os.environ["PATH"] = os.pathsep.join(parts + [current])
    os.environ.setdefault("HF_HOME", str(DATA_DIR / "models"))


setup_bundled_tools()

MAX_UPLOAD_BYTES = int(os.getenv("VOX_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
DOWNLOAD_TIMEOUT = int(os.getenv("VOX_DOWNLOAD_TIMEOUT", "600"))
CACHE_MAX_DAYS = int(os.getenv("VOX_CACHE_DAYS", "30"))
UPLOAD_MAX_AGE_H = int(os.getenv("VOX_UPLOAD_MAX_AGE_H", "6"))
ALLOWED_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac",
                    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v"}

MODELS = {
    "tiny": {"label": "Tiny", "note": "mais rápido, menor precisão", "mult": 0.15},
    "base": {"label": "Base", "note": "rápido", "mult": 0.2},
    "small": {"label": "Small", "note": "equilíbrio (recomendado)", "mult": 0.3},
    "medium": {"label": "Medium", "note": "mais preciso, mais lento", "mult": 0.8},
}

_models = {}
_models_lock = threading.Lock()
_transcribe_lock = threading.Lock()
_cancel = set()
_cancel_lock = threading.Lock()
_argos = None
_argos_lock = threading.Lock()


def validate_url(url: str) -> str:
    """Aceita apenas URLs http(s) — evita que o yt-dlp interprete o link como opção."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Informe um link para transcrever.")
    if url.startswith("-"):
        raise ValueError("Link inválido.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Link inválido. Use um endereço começando com http:// ou https://.")
    return url


def cleanup_old_files(directory: Path, max_age_hours: float):
    """Remove arquivos antigos para não acumular disco."""
    now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for p in entries:
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_hours * 3600:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_translation_package():
    from argostranslate import package as apkg
    from argostranslate import settings as asettings

    for d in list(asettings.package_dirs) + [asettings.downloads_dir, asettings.cache_dir,
                                             asettings.package_data_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    def find():
        return next((p for p in apkg.get_installed_packages()
                     if p.from_code == "en" and p.to_code == "pt"), None)

    pkg = find()
    if pkg:
        return pkg
    apkg.update_package_index()
    available = [p for p in apkg.get_available_packages()
                 if p.from_code == "en" and p.to_code == "pt"]
    if not available:
        raise RuntimeError("Modelo de tradução EN→PT não disponível.")
    archive = available[0].download()
    with zipfile.ZipFile(archive) as z:
        z.extractall(asettings.package_data_dir)
    pkg = find()
    if not pkg:
        raise RuntimeError("Falha ao instalar o modelo de tradução.")
    return pkg


def get_translation():
    global _argos
    with _argos_lock:
        if _argos is None:
            import ctranslate2
            pkg = ensure_translation_package()
            translator = ctranslate2.Translator(
                str(pkg.package_path / "model"), device="cpu", compute_type="int8"
            )
            _argos = (pkg, translator)
        return _argos


def translate_text(text: str) -> str:
    if not text.strip():
        return text
    pkg, translator = get_translation()
    result = translator.translate_batch(
        [pkg.tokenizer.encode(text)], beam_size=4, max_batch_size=8,
        batch_type="examples", replace_unknowns=True,
    )
    return pkg.tokenizer.decode(result[0].hypotheses[0]).strip()


def translate_segments(segments: list, chunk: int = 64):
    pkg, translator = get_translation()
    for start in range(0, len(segments), chunk):
        batch = segments[start:start + chunk]
        texts = [s["text"] for s in batch]
        results = translator.translate_batch(
            [pkg.tokenizer.encode(t) for t in texts], beam_size=4, max_batch_size=32,
            batch_type="examples", replace_unknowns=True,
        )
        for seg, result in zip(batch, results):
            seg["text"] = pkg.tokenizer.decode(result.hypotheses[0]).strip()


def trim_memory():
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass


def get_model(name: str):
    if name not in MODELS:
        raise ValueError(f"modelo inválido: {name}")
    with _models_lock:
        if name not in _models:
            # Mantém só um modelo na memória por vez (evita OOM em máquinas de 8–16 GB).
            if _models:
                _models.clear()
                trim_memory()
            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


@contextmanager
def db():
    """Conexão SQLite: faz commit no sucesso e sempre fecha a conexão.

    Mantido no modo padrão (sem WAL) para evitar conflito entre versões
    diferentes do app abertas ao mesmo tempo no mesmo arquivo.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                language TEXT,
                duration REAL,
                segments INTEGER,
                created_at TEXT NOT NULL,
                text TEXT NOT NULL,
                segments_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def get_setting(key: str, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


TRIAL_LIMIT = int(os.getenv("VOX_TRIAL_LIMIT", "3"))


def license_state() -> dict:
    """Estado da licença: ativa / expirada / trial / bloqueada."""
    stored = get_setting("license")
    if stored:
        try:
            info = license.verify(stored)
            if info.get("expira_em") and info["expira_em"] < date.today().toordinal():
                return {"status": "expirada", "tier": info["tier"]}
            return {"status": "ativa", **info}
        except license.LicenseError:
            pass
    used = int(get_setting("trial_used", "0") or 0)
    if used < TRIAL_LIMIT:
        return {"status": "trial", "restantes": TRIAL_LIMIT - used, "limite": TRIAL_LIMIT}
    return {"status": "bloqueada", "restantes": 0, "limite": TRIAL_LIMIT}


init_db()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def friendly_error(raw: str) -> str:
    low = (raw or "").lower()
    if "private video" in low:
        return "Este vídeo é privado."
    if ("video is unavailable" in low or "video unavailable" in low
            or "has been removed" in low or "not available" in low):
        return "Vídeo indisponível ou removido."
    if "not a bot" in low or "sign in to confirm" in low or "cookies" in low:
        return "O YouTube pediu verificação. Tente mais tarde ou use um arquivo local."
    if "unsupported url" in low or "invalid url" in low:
        return "Link inválido ou não suportado."
    if "timed out" in low or "network" in low or "connection" in low or "getaddrinfo" in low:
        return "Sem conexão com a internet ou tempo esgotado."
    if "ffmpeg" in low:
        return "Falha ao converter o áudio (ffmpeg)."
    if "unable to download" in low or "http error" in low:
        return "Não foi possível baixar o áudio desse link."
    return raw.strip()[:300] or "Erro inesperado."


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def cached_audio(key: str):
    for p in CACHE_DIR.glob(key + ".*"):
        if p.is_file() and p.suffix not in (".json", ".part", ".ytdl") and p.stat().st_size > 0:
            return p
    return None


def cached_title(key: str):
    info = CACHE_DIR / f"{key}.info.json"
    if info.exists():
        try:
            return json.loads(info.read_text(encoding="utf-8")).get("title")
        except Exception:  # noqa: BLE001
            return None
    return None


def yt_dlp_command():
    """yt-dlp do sistema, ou o binário empacotado junto do app."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    for name in ("yt-dlp.exe", "yt-dlp"):
        for folder in tool_dirs():
            candidate = folder / name
            if candidate.exists():
                return [str(candidate)]
    return ["yt-dlp"]


def ffmpeg_path():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for name in ("ffmpeg.exe", "ffmpeg"):
        for folder in tool_dirs():
            candidate = folder / name
            if candidate.exists():
                return str(candidate)
    return None


def download_audio(url: str):
    url = validate_url(url)
    key = url_key(url)
    audio = cached_audio(key)
    title = cached_title(key)
    if audio:
        return audio.resolve(), title, True
    cmd = yt_dlp_command() + ["-x", "--audio-format", "mp3", "--audio-quality", "0",
                              "--no-playlist", "--write-info-json", "--no-warnings",
                              "-o", str(CACHE_DIR / f"{key}.%(ext)s"), url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("O download demorou demais e foi interrompido.")
    except FileNotFoundError:
        raise RuntimeError("yt-dlp não encontrado. Instale o yt-dlp no sistema.")
    if result.returncode != 0:
        raise RuntimeError(friendly_error(result.stderr))
    audio = cached_audio(key)
    if not audio:
        raise RuntimeError("Áudio não encontrado após o download.")
    return audio.resolve(), cached_title(key), False


def fmt_ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def seg_text(seg: dict) -> str:
    speaker = seg.get("speaker")
    return f"{speaker}: {seg['text']}" if speaker else seg["text"]


def to_txt(segments: list) -> str:
    return "\n".join(f"[{fmt_ts(s['start'], '.')[:8]}] {seg_text(s)}" for s in segments)


def to_srt(segments: list) -> str:
    blocks = []
    for i, s in enumerate(segments, 1):
        blocks.append(f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{seg_text(s)}")
    return "\n\n".join(blocks) + "\n"


def to_vtt(segments: list) -> str:
    blocks = ["WEBVTT\n"]
    for s in segments:
        blocks.append(f"{fmt_ts(s['start'], '.')} --> {fmt_ts(s['end'], '.')}\n{seg_text(s)}")
    return "\n\n".join(blocks) + "\n"


def safe_name(title: str, fallback: str = "transcricao") -> str:
    base = re.sub(r"[^\w\-. ]+", "_", (title or fallback), flags=re.UNICODE).strip(" ._")
    return (base[:120] or fallback)


def save_history(title, source, kind, language, duration, segments, text):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO history (title, source, kind, language, duration, segments, created_at, text, segments_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (title, source, kind, language, duration, len(segments),
             datetime.now(timezone.utc).isoformat(), text, json.dumps(segments)),
        )
        return cur.lastrowid


def is_cancelled(job_id: str) -> bool:
    with _cancel_lock:
        return job_id in _cancel


def diarize_audio(audio_path: Path):
    try:
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise RuntimeError(
            "Diarização não instalada. Rode: pip install -r requirements-diarization.txt"
        ) from e
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not token:
        raise RuntimeError(
            "Diarização requer o token do HuggingFace. Defina a variável HF_TOKEN."
        )
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    annotation = pipeline(str(audio_path))
    return [(turn.start, turn.end, label) for turn, _track, label in annotation.itertracks(yield_label=True)]


def assign_speakers(segments: list, turns: list):
    for seg in segments:
        best, best_ov = None, 0.0
        for start, end, label in turns:
            ov = min(seg["end"], end) - max(seg["start"], start)
            if ov > best_ov:
                best, best_ov = label, ov
        seg["speaker"] = best


def transcribe_events(url: str, file_id: str, model_name: str, language: str,
                      diarize: bool, translate: bool, job_id: str):
    try:
        state = license_state()
        if state["status"] == "bloqueada":
            yield sse("error", {"detail": "Você usou as transcrições de teste. Ative sua licença para continuar."})
            return
        if state["status"] == "trial":
            set_setting("trial_used", int(get_setting("trial_used", "0") or 0) + 1)

        title = None
        kind = "file" if file_id else "url"
        source = file_id or url
        upload_path = None

        if file_id:
            if not re.fullmatch(r"[a-f0-9]{32}\.[A-Za-z0-9]+", file_id):
                raise RuntimeError("Arquivo inválido.")
            upload_path = UPLOAD_DIR / file_id
            if not upload_path.exists():
                raise RuntimeError("Arquivo não encontrado. Envie novamente.")
            meta = UPLOAD_DIR / f"{file_id}.json"
            if meta.exists():
                try:
                    title = json.loads(meta.read_text(encoding="utf-8")).get("name")
                except Exception:  # noqa: BLE001
                    title = None
            title = title or upload_path.name
            audio_path = upload_path
            yield sse("status", {"msg": "Preparando arquivo..."})
        else:
            if not url:
                raise RuntimeError("Informe um link ou envie um arquivo.")
            yield sse("status", {"msg": "Verificando cache..."})
            if cached_audio(url_key(url)):
                yield sse("status", {"msg": "Áudio em cache. Iniciando transcrição..."})
            else:
                yield sse("status", {"msg": "Baixando áudio..."})
            audio_path, title, _was_cached = download_audio(url)
            title = title or "Transcrição"

        if job_id and is_cancelled(job_id):
            yield sse("cancelled", {"msg": "Transcrição cancelada."})
            return

        yield sse("status", {"msg": f"Carregando modelo {MODELS[model_name]['label']}..."})
        with _transcribe_lock:
            model = get_model(model_name)

            yield sse("status", {"msg": "Transcrevendo..."})
            segments_iter, info = model.transcribe(
                str(audio_path),
                beam_size=5,
                language=language or None,
                vad_filter=True,
            )
            segments = []
            for s in segments_iter:
                if job_id and is_cancelled(job_id):
                    yield sse("cancelled", {"msg": "Transcrição cancelada."})
                    return
                text = s.text.strip()
                if not text:
                    continue
                segments.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": text})
                pct = min(100, int(s.end / info.duration * 100)) if info.duration else 0
                yield sse("progress", {"pct": pct, "text": text})

        if diarize and segments:
            yield sse("status", {"msg": "Identificando falantes (diarização)..."})
            try:
                turns = diarize_audio(Path(audio_path))
                assign_speakers(segments, turns)
                yield sse("status", {"msg": "Falantes identificados."})
            except Exception as e:  # noqa: BLE001
                yield sse("warning", {"msg": str(e)})

        out_language = info.language
        if translate and segments and not (info.language or "").startswith("pt"):
            yield sse("status", {"msg": "Traduzindo para PT-BR (primeira vez pode baixar o modelo)..."})
            try:
                translate_segments(segments)
                out_language = "pt"
                yield sse("status", {"msg": "Tradução concluída."})
            except Exception as e:  # noqa: BLE001
                yield sse("warning", {"msg": f"Tradução indisponível: {e}"})

        text = to_txt(segments)
        history_id = save_history(
            (title or "Transcrição")[:300], source, kind, out_language,
            round(info.duration or 0, 2), segments, text,
        )
        yield sse("done", {
            "id": history_id,
            "title": title,
            "filename": safe_name(title),
            "text": text,
            "language": out_language,
            "duration": info.duration,
            "segments": len(segments),
        })
    except Exception as e:  # noqa: BLE001
        yield sse("error", {"detail": friendly_error(str(e))})
    finally:
        if job_id:
            with _cancel_lock:
                _cancel.discard(job_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Limpeza de arquivos antigos (uploads e cache de áudio).
    cleanup_old_files(UPLOAD_DIR, UPLOAD_MAX_AGE_H)
    cleanup_old_files(CACHE_DIR, CACHE_MAX_DAYS * 24)
    yield


app = FastAPI(title="Vox", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def block_cross_origin(request, call_next):
    """Impede que outro site no navegador use o app local (CSRF)."""
    origin = request.headers.get("origin")
    if origin:
        try:
            origin_host = urlparse(origin).netloc
        except ValueError:
            origin_host = ""
        host = request.headers.get("host", "")
        if origin_host and host and origin_host != host:
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "templates" / "index.html")


@app.get("/docs")
def docs():
    return FileResponse(BASE_DIR / "templates" / "docs.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
def favicon():
    icon = BASE_DIR / "assets" / "favicon.png"
    if icon.exists():
        return FileResponse(icon, media_type="image/png")
    from fastapi import Response
    return Response(status_code=204)


@app.get("/api/models")
def models():
    return [{"id": k, **v} for k, v in MODELS.items()]


class LicenseRequest(BaseModel):
    key: str = ""


@app.get("/api/license")
def license_info():
    state = license_state()
    state["primeira_vez"] = not get_setting("boas_vindas_vista")
    return state


@app.post("/api/license/boas-vindas")
def license_welcome():
    set_setting("boas_vindas_vista", "1")
    return {"ok": True}


@app.post("/api/license/activate")
def license_activate(req: LicenseRequest):
    try:
        info = license.verify(req.key)
    except license.LicenseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    set_setting("license", license.normalize(req.key))
    return {"status": "ativa", **info}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado.")
    file_id = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / file_id
    size = 0
    try:
        with dest.open("wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo muito grande. Limite: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Falha ao salvar o arquivo.")
    (UPLOAD_DIR / f"{file_id}.json").write_text(
        json.dumps({"name": file.filename or "arquivo"}), encoding="utf-8"
    )
    cleanup_old_files(UPLOAD_DIR, UPLOAD_MAX_AGE_H)
    return {"file_id": file_id, "name": file.filename, "size": size}


@app.post("/api/cancel/{job_id}")
def cancel(job_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="job_id inválido.")
    with _cancel_lock:
        _cancel.add(job_id)
    return {"cancelled": True}


@app.get("/api/transcribe/stream")
def transcribe_stream(
    url: str = "",
    file_id: str = "",
    model: str = "small",
    language: str = "",
    diarize: bool = False,
    translate: bool = False,
    job_id: str = "",
):
    if model not in MODELS:
        raise HTTPException(status_code=400, detail="Modelo inválido.")
    if not url and not file_id:
        raise HTTPException(status_code=400, detail="Informe um link ou envie um arquivo.")
    if file_id and not re.fullmatch(r"[a-f0-9]{32}\.[A-Za-z0-9]+", file_id):
        raise HTTPException(status_code=400, detail="Arquivo inválido.")
    if url:
        try:
            url = validate_url(url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return StreamingResponse(
        transcribe_events(url.strip(), file_id, model, language, diarize, translate, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/history")
def history_list():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, source, kind, language, duration, segments, created_at"
            " FROM history ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/history/{item_id}")
def history_item(item_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM history WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada.")
    data = dict(row)
    data["segments_json"] = json.loads(data["segments_json"])
    return data


@app.delete("/api/history/{item_id}")
def history_delete(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
    return {"deleted": True}


@app.get("/api/history/{item_id}/export")
def history_export(item_id: int, fmt: str = "txt"):
    with db() as conn:
        row = conn.execute("SELECT * FROM history WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada.")
    segments = json.loads(row["segments_json"])
    fmt = fmt.lower()
    if fmt == "srt":
        content, media, ext = to_srt(segments), "application/x-subrip", "srt"
    elif fmt == "vtt":
        content, media, ext = to_vtt(segments), "text/vtt", "vtt"
    else:
        content, media, ext = row["text"], "text/plain; charset=utf-8", "txt"
    stem = Path(row["title"]).stem or row["title"]
    name = f"{safe_name(stem)}.{ext}"
    ascii_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return PlainTextResponse(content, media_type=media,
                             headers={"Content-Disposition": disposition})


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": "whisper-small", "object": "model", "created": 0, "owned_by": "local"}],
    }


@app.post("/v1/audio/transcriptions")
async def openai_transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-small"),
    language: str = Form(""),
    response_format: str = Form("json"),
):
    tmp = tempfile.mkdtemp(prefix="vox_api_")
    try:
        suffix = Path(file.filename or "audio").suffix or ".bin"
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        with _transcribe_lock:
            segments_iter, info = get_model("small").transcribe(
                str(path), beam_size=5, language=language or None, vad_filter=True
            )
            text = " ".join(s.text.strip() for s in segments_iter).strip()
        if response_format == "text":
            return PlainTextResponse(text)
        return {"text": text, "language": info.language, "duration": info.duration}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=friendly_error(str(e)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
