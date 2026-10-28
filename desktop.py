#!/usr/bin/env python3
"""Vox — aplicativo de desktop (janela nativa) para Windows e Linux.

Uso:
    python desktop.py            # abre a janela do app
    python desktop.py --no-window # só sobe o servidor (testes/CI)
    python desktop.py --docs      # abre a documentação
"""
import logging
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
WINDOW_TITLE = "Vox — Transcrição local"
LOG_NAME = "vox.log"


def ensure_stdio():
    """Em app sem console (Windows) sys.stdout/stderr são None e quebram o logging."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass


def setup_logging():
    """Log em arquivo — o cliente consegue enviar se algo der errado."""
    try:
        from app import DATA_DIR
        log_path = Path(DATA_DIR) / LOG_NAME
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(log_path, encoding="utf-8")],
            force=True,
        )
        return log_path
    except Exception:  # noqa: BLE001
        return None


def show_error(message: str):
    """Mostra o erro numa janela (Windows) ou no terminal (Linux)."""
    print(message, file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Vox", 0x10)
        except Exception:  # noqa: BLE001
            pass


def free_port(preferred: int = 8000) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


def preferred_gui():
    """Backend de janela nativa por plataforma."""
    if os.name == "nt":
        return "edgechromium"
    return "qt6"  # PySide6 vem por pip: não depende de GTK/WebKit do sistema


def start_server(port: int):
    import uvicorn
    from app import app

    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,  # sem console o dictConfig padrão do uvicorn quebra
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server


def main() -> int:
    ensure_stdio()
    log_path = setup_logging()
    no_window = "--no-window" in sys.argv
    only_docs = "--docs" in sys.argv
    port = free_port()
    server = start_server(port)
    url = f"http://{HOST}:{port}/"
    if only_docs:
        url = f"http://{HOST}:{port}/docs"
    print(f"Vox rodando em {url}", flush=True)
    logging.info("Vox iniciado em %s", url)

    if no_window:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        server.should_exit = True
        return 0

    try:
        import webview
    except ImportError as e:
        logging.warning("pywebview indisponivel: %s", e)
        webview = None

    if webview is not None:
        try:
            window = webview.create_window(
                "Vox — Documentação" if only_docs else WINDOW_TITLE,
                url, width=1180, height=860, min_size=(880, 640)
            )
            window.events.closed += lambda: setattr(server, "should_exit", True)
            webview.start(gui=preferred_gui())
            server.should_exit = True
            return 0
        except Exception as e:  # noqa: BLE001
            logging.exception("janela nativa indisponivel")
            print(f"Janela nativa indisponivel ({e}). Abrindo no navegador.", flush=True)

    print("Abrindo no navegador padrao...", flush=True)
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.should_exit = True
    return 0


if __name__ == "__main__":
    ensure_stdio()
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        try:
            from app import DATA_DIR
            log_file = Path(DATA_DIR) / LOG_NAME
            with log_file.open("a", encoding="utf-8") as f:
                f.write(detail)
        except Exception:  # noqa: BLE001
            pass
        show_error(
            "O Vox encontrou um erro ao iniciar.\n\n"
            f"Detalhes salvos em:\n{log_file if 'log_file' in dir() else 'vox.log'}"
        )
        sys.exit(1)
