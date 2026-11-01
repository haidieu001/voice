"""
ElevenLabs Voice Pro - Desktop Application
Launches Flask backend + pywebview desktop window.
"""

import sys
import os
import threading
import time
import logging
import webview

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("desktop")

# Constants
APP_TITLE = "ElevenLabs Voice Pro"
APP_WIDTH = 1360
APP_HEIGHT = 860
HOST = "127.0.0.1"
PORT = 5055


def start_flask():
    """Start Flask server in background thread."""
    from app import app
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def main():
    log.info("Starting %s...", APP_TITLE)

    # Start Flask in background thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait for Flask to be ready
    import urllib.request
    for i in range(30):
        try:
            urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=1)
            log.info("Flask server ready!")
            break
        except Exception:
            time.sleep(0.3)
    else:
        log.error("Flask server failed to start!")
        sys.exit(1)

    # Create desktop window
    window = webview.create_window(
        title=APP_TITLE,
        url=f"http://{HOST}:{PORT}",
        width=APP_WIDTH,
        height=APP_HEIGHT,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
    )

    log.info("Opening desktop window...")

    # Start webview (blocks until window is closed)
    webview.start(
        debug=False,
        private_mode=False,
    )

    log.info("Window closed. Exiting.")
    os._exit(0)


if __name__ == "__main__":
    main()
