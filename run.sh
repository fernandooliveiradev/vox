#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Ambiente virtual não encontrado. Crie com:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements-app.txt"
  exit 1
fi

for dep in ffmpeg yt-dlp; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    echo "Aviso: '$dep' não encontrado no PATH. Algumas funções podem falhar."
  fi
done

if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "Vox já está rodando em http://127.0.0.1:${PORT}"
else
  .venv/bin/uvicorn app:app --host 127.0.0.1 --port "${PORT}" &
  sleep 2
  echo "Vox rodando em http://127.0.0.1:${PORT}"
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
fi
