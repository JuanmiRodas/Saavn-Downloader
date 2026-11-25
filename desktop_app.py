"""Native desktop launcher for Saavn-Downloader Studio (.app / macOS)."""

from __future__ import annotations

import os
import sys
import time
import webbrowser

from scripts.web_ui import start_web_server_background


def _default_music_dir() -> str:
    music_dir = os.path.join(os.path.expanduser("~"), "Music", "Saavn-Downloader")
    try:
        os.makedirs(music_dir, exist_ok=True)
    except OSError:
        music_dir = os.path.expanduser("~")
    return music_dir


def main() -> None:
    out_folder = _default_music_dir()
    server, url = start_web_server_background(
        host="127.0.0.1",
        port=0,
        out_folder=out_folder,
    )

    try:
        import webview  # type: ignore

        webview.create_window(
            title="Saavn-Downloader Studio",
            url=url,
            width=1360,
            height=880,
            min_size=(960, 640),
            resizable=True,
        )
        webview.start(private_mode=False)
    except Exception:
        # Fallback: open in default browser and keep server alive
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
