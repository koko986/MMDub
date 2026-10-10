import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 7860
URL = f"http://{HOST}:{PORT}"


def port_is_open() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, PORT)) == 0


def is_our_server() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/status", timeout=1) as response:
            return b'"local_only":true' in response.read().replace(b" ", b"")
    except Exception:
        return False


def open_when_ready() -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{URL}/api/status", timeout=1):
                webbrowser.open(URL)
                return
        except Exception:
            time.sleep(0.25)


if __name__ == "__main__":
    if port_is_open():
        if is_our_server():
            webbrowser.open(URL)
            print(f"MMDub is already running at {URL}")
            raise SystemExit(0)
        print(f"Port {PORT} is already used by another app. Close it, then try again.")
        input("Press Enter to close...")
        raise SystemExit(1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    print("Starting MMDub...")
    print(f"Open: {URL}")
    print("Keep this window open. Press Ctrl+C to stop MMDub.")
    uvicorn.run("app:app", host=HOST, port=PORT, log_level="info")
