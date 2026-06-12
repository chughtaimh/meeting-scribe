#!/usr/bin/env python3
"""Meeting Scribe entry point.

Starts the local server on 127.0.0.1 and opens the app in the default browser.
If Meeting Scribe is already running, it simply opens the browser window.
"""

import socket
import sys
import threading
import time
import webbrowser

from scribe import config
from scribe.server import create_app


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_scribe(port: int) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/state" % port, timeout=1.5) as r:
            import json
            return json.loads(r.read().decode()).get("app") == "meeting-scribe"
    except Exception:
        return False


def main():
    cfg = config.load()
    base_port = int(cfg.get("port") or 5723)

    if port_in_use(base_port) and is_scribe(base_port):
        webbrowser.open("http://127.0.0.1:%d" % base_port)
        print("Meeting Scribe is already running — opened the browser window.")
        return

    port = base_port
    for candidate in range(base_port, base_port + 20):
        if not port_in_use(candidate):
            port = candidate
            break

    app = create_app()
    url = "http://127.0.0.1:%d" % port
    config.log("starting Meeting Scribe on %s" % url)
    print("Meeting Scribe → %s   (keep this window open while using the app)" % url)

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
