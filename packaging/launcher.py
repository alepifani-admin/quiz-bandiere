"""QuiZ Bandiere — desktop launcher (frozen macOS app).

Starts the local game server, opens the game in the default browser and shows
a small control window (reopen browser / quit). All writable data lives under
``~/Library/Application Support/QuiZ Bandiere`` so the desktop app is fully
separate from the web development instance and works offline on first run.

This module is the PyInstaller entry point: it only runs inside the packaged
.app, never in the web version.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import webbrowser
from pathlib import Path

APP_DISPLAY_NAME = "QuiZ Bandiere"
SUPPORT_FOLDER = "QuiZ Bandiere"
APP_VERSION = "0.2.0"

# NOTE: `app.main` (and its whole import graph) is imported inside main()
# AFTER the environment is configured; the PyInstaller spec collects every
# app.* module explicitly, so packaging does not depend on this module.


def _bundle_dir() -> Path:
    """Where the packaged app keeps its read-only files."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _seed_dir() -> Path:
    return _bundle_dir() / "data"


def _app_support() -> Path:
    return Path.home() / "Library" / "Application Support" / SUPPORT_FOLDER


def setup_data() -> Path:
    """Copy the seeded data into Application Support once, then return it."""
    support = _app_support()
    data_dir = support / "data"
    if not data_dir.exists():
        seed = _seed_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("baseline", "cache", "review"):
            src = seed / name
            if src.is_dir() and not (data_dir / name).exists():
                shutil.copytree(src, data_dir / name)
        # the desktop app keeps its own, fresh game state
        (data_dir / "leaderboard.json").write_text(
            '{"version": 1, "scores": []}', encoding="utf-8"
        )
        (data_dir / "fifa_quiz.db").touch()
    return data_dir


def configure_env(data_dir: Path, port: int) -> None:
    os.environ["FIFAQUIZ_DATA_BASELINE_DIR"] = str(data_dir / "baseline")
    os.environ["FIFAQUIZ_DATA_CACHE_DIR"] = str(data_dir / "cache")
    os.environ["FIFAQUIZ_LEADERBOARD_FILE"] = str(data_dir / "leaderboard.json")
    os.environ["FIFAQUIZ_DB_PATH"] = str(data_dir / "fifa_quiz.db")
    os.environ.setdefault("FIFAQUIZ_DEBUG", "false")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def load_app():
    """Import the backend app now that the environment is configured."""
    from app import main as _app_main  # noqa: PLC0415 - env first

    return _app_main.app


def run_server(port: int):
    import uvicorn  # noqa: PLC0415

    config = uvicorn.Config(load_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _serve() -> None:
        server.run()

    threading.Thread(target=_serve, daemon=True, name="quiz-server").start()
    return server


def _wait_until_ready(port: int, timeout: float = 30.0) -> None:
    import time  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1
            ) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("server did not become ready in time")


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - never crash for a browser issue
        pass


def _quit_app(root, server) -> None:
    server.should_exit = True
    try:
        root.destroy()
    finally:
        os._exit(0)  # noqa: PLR1722 - windowed app has no other lifecycle


def main() -> int:
    data_dir = setup_data()
    port = free_port()
    configure_env(data_dir, port)
    server = run_server(port)
    _wait_until_ready(port)
    url = f"http://127.0.0.1:{port}"
    open_browser(url)

    # Small control window: reopen the browser or quit the whole app.
    import tkinter as tk  # noqa: PLC0415

    root = tk.Tk()
    root.title(APP_DISPLAY_NAME)
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: _quit_app(root, server))

    tk.Label(
        root,
        text=f"{APP_DISPLAY_NAME} v{APP_VERSION}",
        font=("Helvetica Neue", 15, "bold"),
    ).pack(pady=(14, 2))
    tk.Label(
        root,
        text="Il gioco è aperto nel browser. Puoi rilanciarlo o chiudere l'app.",
        wraplength=300,
        justify="left",
        fg="#555",
        padx=18,
    ).pack()
    tk.Label(root, text=url, fg="#0a5ec2").pack(pady=(0, 10))

    buttons = tk.Frame(root)
    buttons.pack(pady=(2, 14))
    tk.Button(
        buttons,
        text="Apri nel browser",
        command=lambda: open_browser(url),
        padx=14,
        pady=4,
    ).pack(side="left", padx=6)
    tk.Button(
        buttons,
        text="Esci",
        command=lambda: _quit_app(root, server),
        padx=14,
        pady=4,
    ).pack(side="left", padx=6)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
