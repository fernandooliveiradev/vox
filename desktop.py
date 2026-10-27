#!/usr/bin/env python3
"""Vox — aplicativo de desktop (janela nativa) para Windows e Linux.

Uso:
    python desktop.py            # abre a janela do app
    python desktop.py --no-window # só sobe o servidor (testes/CI)
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
WINDOW_TITLE = "Vox — Transcrição local"


def free_port(preferred: int = 8000) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


def start_server(port: int):
    import uvicorn
    from app import app

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server


def preferred_gui():
    """Backend de janela nativa por plataforma."""
    if os.name == "nt":
        return "edgechromium"
    return "qt6"  # PySide6 vem por pip: não depende de GTK/WebKit do sistema


def main() -> int:
    no_window = "--no-window" in sys.argv
    port = free_port()
    server = start_server(port)
    url = f"http://{HOST}:{port}/"
    print(f"Vox rodando em {url}", flush=True)

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
    except ImportError:
        webview = None

    if webview is not None:
        try:
            window = webview.create_window(
                WINDOW_TITLE, url, width=1180, height=860, min_size=(880, 640)
            )
            window.events.closed += lambda: setattr(server, "should_exit", True)
            webview.start(gui=preferred_gui())
            server.should_exit = True
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"Janela nativa indisponível ({e}). Abrindo no navegador.", flush=True)

    print("Abrindo no navegador padrão...", flush=True)
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
