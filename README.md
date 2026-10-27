# Vox

Webapp local de transcrição de vídeos e áudios com [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Roda 100% na sua máquina — nenhum áudio é enviado para servidores.

## Rodar

```bash
cd /home/fernandopaglacci/Projects/fernando_dev/faster-whisper
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
# abra http://127.0.0.1:8000
```

Dependências do app:

```bash
.venv/bin/pip install -r requirements-app.txt
```

Requisitos externos: `ffmpeg` e `yt-dlp` no PATH.

## Funcionalidades

- Link do YouTube/Instagram (via yt-dlp) ou upload de arquivo local (arrastar e soltar)
- Progresso em tempo real (SSE) e cancelamento
- Modelos: tiny, base, small, medium
- Exportação: TXT, SRT, VTT
- Histórico local (SQLite em `data/vox.db`)
- Cache de áudio baixado (não baixa duas vezes)
- Nome do arquivo baseado no título do vídeo
- Mensagens de erro amigáveis
- API compatível com OpenAI: `GET /v1/models`, `POST /v1/audio/transcriptions`
- Documentação do usuário em `http://127.0.0.1:8000/docs`

## Tradução EN→PT-BR — opcional

Usa Argos Translate (offline, leve, mesma engine CTranslate2). Instale sem as
dependências pesadas (torch/stanza):

```bash
.venv/bin/pip install --no-deps argostranslate sentencepiece
.venv/bin/python setup_translation.py
```

Ou simplesmente rode `setup_translation.py` — ele instala o necessário e baixa
o modelo EN→PT (~100 MB) uma vez. Depois é só marcar **Traduzir para PT-BR** no app.

Observação: o pacote público `en→pt` do Argos é português genérico e às vezes
usa formas de PT-PT ("vosso", "estás"). Para PT-BR estrito, é preciso um modelo
maior (NLLB) ou um LLM local.

## Diarização (quem falou) — opcional

Requer dependências extras e um token do HuggingFace (modelos gateados):

```bash
.venv/bin/pip install -r requirements-diarization.txt
export HF_TOKEN=seu_token
```

No app, marque a opção **Diarização**. Se não estiver configurada, a transcrição
continua normalmente e o app apenas avisa.

## Testes

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tests/test_api.py
```

36 testes ponta a ponta: páginas, health, upload, validações de link/arquivo,
transcrição, tradução, histórico, exclusão, exportações (TXT/SRT/VTT) e API
compatível com OpenAI.

## Variáveis de ambiente (opcionais)

| Variável | Padrão | Função |
|---|---|---|
| `VOX_DATA_DIR` | `./data` | Onde ficam banco, cache e uploads |
| `VOX_MAX_UPLOAD_MB` | `2048` | Tamanho máximo de upload |
| `VOX_DOWNLOAD_TIMEOUT` | `600` | Timeout do download (segundos) |
| `VOX_CACHE_DAYS` | `30` | Validade do cache de áudio |
| `VOX_UPLOAD_MAX_AGE_H` | `6` | Tempo que o upload fica disponível |
| `PORT` | `8000` | Porta do servidor (usada pelo `run.sh`) |

## Segurança

- Servidor escuta apenas em `127.0.0.1` (não expõe a rede).
- Requisições de outros sites (CSRF) são bloqueadas.
- Links precisam ser `http(s)`; uploads são limitados por extensão e tamanho.
- Nada é enviado para servidores externos.

## App de desktop (Windows e Linux)

O mesmo código vira um aplicativo com janela própria:

```bash
# testar localmente (servidor + interface, sem janela)
.venv/bin/python desktop.py --no-window

# build Linux (gera dist/Vox/Vox com ffmpeg e yt-dlp embutidos)
./build_linux.sh

# build Windows (gera dist/Vox/Vox.exe + instalador com Inno Setup)
build_windows.bat
```

- Janela nativa via **pywebview** (WebView2 no Windows). Sem WebKit no Linux, o app
  abre no navegador padrão automaticamente.
- Dados do usuário em `~/.local/share/vox` (Linux) ou `%LOCALAPPDATA%\Vox` (Windows):
  banco, cache, uploads e modelos.
- **ffmpeg** e **yt-dlp** vão embutidos em `bin/` (build baixa o yt-dlp oficial).
- O modelo Whisper **não** é embutido: baixa no primeiro uso (~500 MB para `small`).
- `installer.iss` gera o instalador Windows; `.github/workflows/release.yml` compila
  os dois sistemas no GitHub Actions a cada tag `v*`.

## Testes

```bash
.venv/bin/python tests/test_api.py     # 36 testes de API
./build_linux.sh                        # build + teste de fumaça do executável
```

## Estrutura

```
app.py                  # FastAPI + faster-whisper
desktop.py              # app de desktop (janela nativa)
templates/index.html    # UI do app
templates/docs.html     # documentação do usuário
tests/test_api.py       # suíte de testes ponta a ponta
run.sh                  # atalho para iniciar
build_linux.sh          # build do AppImage/pasta
build_windows.bat       # build do .exe + instalador
installer.iss           # instalador Windows (Inno Setup)
.github/workflows/release.yml
requirements-app.txt
requirements-desktop.txt
requirements-diarization.txt
requirements-translation.txt
requirements-dev.txt
data/                   # banco, cache e uploads (gerado)
```
