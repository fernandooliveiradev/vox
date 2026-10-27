@echo off
setlocal
cd /d "%~dp0"

set VENV=.venv
set NAME=Vox
set OUT=dist

echo >> Instalando dependencias de desktop
%VENV%\Scripts\pip.exe install -q -r requirements-desktop.txt
%VENV%\Scripts\pip.exe install -q imageio-ffmpeg

echo >> Limpando builds anteriores
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo >> Gerando executavel (PyInstaller)
%VENV%\Scripts\pyinstaller.exe --noconfirm --clean --onedir --windowed --name %NAME% ^
  --icon assets\vox.ico ^
  --add-data "templates;templates" --add-data "assets;assets" ^
  --collect-all faster_whisper --collect-all ctranslate2 ^
  --collect-all av --collect-all onnxruntime --collect-all tokenizers ^
  --collect-all argostranslate --collect-all sentencepiece ^
  --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on --hidden-import uvicorn.protocols.http.h11_impl ^
  desktop.py

echo >> Copiando ffmpeg e yt-dlp
if not exist "%OUT%\%NAME%\bin" mkdir "%OUT%\%NAME%\bin"
%VENV%\Scripts\python.exe -c "import os,shutil,imageio_ffmpeg;shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), os.path.join('dist','Vox','bin','ffmpeg.exe')); print('  ffmpeg copiado')"
curl -fsSL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -o "%OUT%\%NAME%\bin\yt-dlp.exe"
echo   yt-dlp copiado

echo >> Baixando o WebView2 Runtime (necessario para a janela nativa)
curl -fsSL "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -o "%OUT%\WebView2Bootstrapper.exe" || echo   (aviso: download do WebView2 falhou)

echo >> Concluido: %OUT%\%NAME%\Vox.exe

set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  echo >> Gerando instalador (Inno Setup)
  "%ISCC%" installer.iss
) else (
  echo >> Inno Setup nao encontrado. Instale em https://jrsoftware.org/isdl.php para gerar o instalador.
)
