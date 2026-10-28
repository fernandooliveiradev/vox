#!/usr/bin/env bash
# Instala o Vox no Linux (~/.local) e cria o atalho no menu/área de trabalho.
set -euo pipefail
cd "$(dirname "$0")"

APP_DIR="${VOX_APP_DIR:-$HOME/.local/opt/Vox}"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

echo ">> Instalando em $APP_DIR"
mkdir -p "$APP_DIR"
# copia o conteúdo da pasta Vox (já extraído) para o destino
cp -a Vox/. "$APP_DIR/"
chmod +x "$APP_DIR/Vox" "$APP_DIR/bin/"* 2>/dev/null || true

echo ">> Ícone"
mkdir -p "$ICON_DIR"
cp -f assets/vox-256.png "$ICON_DIR/vox.png" 2>/dev/null || true

echo ">> Atalho no menu"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/vox.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Vox
Comment=Transcrição de áudio e vídeo local
Exec=$APP_DIR/Vox
Icon=vox
Terminal=false
Categories=Utility;AudioVideo;
DESKTOP
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Pronto! Procure por 'Vox' no menu de aplicativos (ou rode: $APP_DIR/Vox)"
