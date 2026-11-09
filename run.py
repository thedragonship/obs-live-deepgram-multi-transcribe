from __future__ import annotations

import socket
import threading
import time
import webbrowser
from typing import Any

import uvicorn

from app import app
from config_store import DEFAULT_PORT, load_settings, save_settings


HOST = "127.0.0.1"
def load_port() -> int:
    try:
        value = int(load_settings().get("port", DEFAULT_PORT))
        return value if 1024 <= value <= 65535 else DEFAULT_PORT
    except (ValueError, TypeError):
        return DEFAULT_PORT


def save_port(port: int) -> None:
    save_settings(port=port)


def find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    # The frozen PyInstaller build does not need Uvicorn's dictConfig formatter
    # setup (which can fail when formatter classes are not bundled). Keep the
    # server quiet in the desktop app and let startup errors surface via state.
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level="warning", log_config=None)
    )
    app.state.active_port = port
    app.state.active_server = server
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started or not thread.is_alive():
            break
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError(f"Impossible de démarrer le serveur local sur le port {port}.")
    return server, thread


def monitor_restarts(window: Any, initial_server: uvicorn.Server, initial_thread: threading.Thread) -> None:
    server, thread = initial_server, initial_thread
    while True:
        thread.join()
        if getattr(app.state, "app_closing", False):
            return
        next_port = int(getattr(app.state, "requested_port", app.state.active_port))
        try:
            server, thread = start_server(next_port)
            save_port(next_port)
            new_url = f"http://{HOST}:{next_port}/"
            if window is None:
                webbrowser.open(new_url)
            else:
                window.load_url(new_url)
        except Exception as exc:
            print(f"Redémarrage impossible : {exc}")
            fallback = load_port()
            if fallback == next_port:
                fallback = DEFAULT_PORT
            time.sleep(0.5)
            server, thread = start_server(fallback)
            if window is None:
                webbrowser.open(f"http://{HOST}:{fallback}/")
            else:
                window.load_url(f"http://{HOST}:{fallback}/")


def main() -> None:
    port = load_port()
    try:
        server, thread = start_server(port)
    except RuntimeError:
        port = find_available_port()
        server, thread = start_server(port)
        save_port(port)

    try:
        import webview

        window = webview.create_window(
            "OBS Live Deepgram Multi Transcribe",
            f"http://{HOST}:{port}/",
            width=1040,
            height=760,
            min_size=(760, 560),
            resizable=True,
        )
        threading.Thread(target=monitor_restarts, args=(window, server, thread), daemon=True).start()
        webview.start()
    except ImportError:
        webbrowser.open(f"http://{HOST}:{port}/")
        monitor_restarts(None, server, thread)
    finally:
        app.state.app_closing = True
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
