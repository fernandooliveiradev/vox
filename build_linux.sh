#!/usr/bin/env bash
# Build do Vox para Linux (AppImage / pasta executável).
set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
NAME="Vox"
OUT="dist"

echo ">> Instalando dependências de desktop"
"$VENV"/bin/pip install -q pyinstaller imageio-ffmpeg
# Janela nativa no Linux via PySide6 (não depende de GTK/WebKit do sistema).
"$VENV"/bin/pip install -q PySide6 qtpy
# pywebview instalado sem dependências pesadas (evita compilar PyGObject)
"$VENV"/bin/pip install -q --no-deps pywebview bottle proxy_tools typing_extensions 2>/dev/null || true

echo ">> Limpando builds anteriores"
rm -rf build "$OUT"

echo ">> Gerando executável (PyInstaller)"
"$VENV"/bin/pyinstaller --noconfirm --clean --onedir --name "$NAME" \
  --icon assets/vox.png \
  --add-data "templates:templates" --add-data "assets:assets" \
  --collect-all faster_whisper --collect-all ctranslate2 \
  --collect-all av --collect-all onnxruntime --collect-all tokenizers \
  --collect-all argostranslate --collect-all sentencepiece \
  --collect-all PySide6 --collect-all qtpy --collect-all webview \
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtSql --exclude-module PySide6.QtTest \
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization \
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtDesigner \
  --exclude-module PySide6.QtSerialPort --exclude-module PySide6.QtBluetooth \
  --exclude-module PySide6.QtNfc --exclude-module PySide6.QtSpatialAudio \
  --exclude-module PySide6.QtTextToSpeech --exclude-module PySide6.QtWebSockets \
  --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on --hidden-import uvicorn.protocols.http.h11_impl \
  desktop.py

echo ">> Copiando ffmpeg e yt-dlp"
mkdir -p "$OUT/$NAME/bin"
"$VENV"/bin/python - <<'PY'
import os, shutil
import imageio_ffmpeg
src = imageio_ffmpeg.get_ffmpeg_exe()
dst = os.path.join("dist", "Vox", "bin", "ffmpeg")
shutil.copy2(src, dst)
os.chmod(dst, 0o755)
print("  ffmpeg ->", dst)
PY
curl -fsSL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux" \
  -o "$OUT/$NAME/bin/yt-dlp"
chmod +x "$OUT/$NAME/bin/yt-dlp"
echo "  yt-dlp -> $OUT/$NAME/bin/yt-dlp"

echo ">> Teste rápido do executável"
"$OUT/$NAME/$NAME" --no-window >/tmp/vox-build-test.log 2>&1 &
VOX_PID=$!
sleep 8
VOX_URL=$(grep -o 'http://127.0.0.1:[0-9]*' /tmp/vox-build-test.log | head -1)
if [ -n "$VOX_URL" ] && curl -fsS --max-time 5 "$VOX_URL/health" >/dev/null 2>&1; then
  echo "  servidor respondeu OK em $VOX_URL"
  curl -fsS --max-time 5 "$VOX_URL/" | grep -q "Transcrever" && echo "  interface carregada OK"
else
  echo "  ATENÇÃO: servidor não respondeu (veja /tmp/vox-build-test.log)"
fi
kill "$VOX_PID" 2>/dev/null || true

echo ">> Reduzindo tamanho do pacote (Qt)"
QT_DIR="$OUT/$NAME/_internal/PySide6/Qt"
if [ -d "$QT_DIR" ]; then
  # traduções: mantém apenas português
  find "$QT_DIR/translations" -type f ! -name 'pt_BR*' ! -name 'qtbase_pt*' -delete 2>/dev/null || true
  # módulos QML e plugins opcionais
  rm -rf "$QT_DIR/qml" "$QT_DIR/assistant" "$QT_DIR/qmlformat" "$QT_DIR/qmlls" 2>/dev/null || true
  # módulos Qt que o app não usa
  for m in Qt3D* QtCharts* QtDataVisualization* QtPdf* QtDesigner* QtHelp* \
           QtBluetooth* QtNfc* QtSql* QtTest* QtTextToSpeech* QtSpatialAudio* \
           QtWebSockets* QtMultimedia* QtSerialPort* QtPositioning* QtVirtualKeyboard* \
           QtQuick3D* QtQuick* QtQml* QtQuickWidgets*; do
    rm -rf "$QT_DIR/lib/$m"* "$QT_DIR/plugins/$m"* 2>/dev/null || true
  done
  # cópias duplicadas de bibliotecas versionadas (o loader usa a com versão)
  find "$QT_DIR/lib" -maxdepth 1 -type f -name '*.so' -delete 2>/dev/null || true
fi

echo ">> Concluído: $OUT/$NAME/$NAME ($(du -sh "$OUT/$NAME" | cut -f1))"
if command -v appimagetool >/dev/null 2>&1; then
  echo "   (appimagetool encontrado — AppImage pode ser gerado)"
fi
