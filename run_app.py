"""Launch the calculator: start the API server and open the browser."""

import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8765


def _open():
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    threading.Timer(1.2, _open).start()
    uvicorn.run("server.app:app", host=HOST, port=PORT, log_level="warning")
