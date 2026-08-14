#!/usr/bin/env python3
"""Open the Learning Hub with local blueprint review enabled."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
import webbrowser


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from review_server.http_api import ReviewApplication, ReviewHTTPServer  # noqa: E402
from review_server.repository import Repository, RepositoryError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the Learning Hub on loopback with editorial review tools.",
    )
    parser.add_argument("--repo", default=str(SCRIPT_DIR.parent), help="Learning Hub repository root")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses an available port")
    parser.add_argument("--no-open", action="store_true", help="Do not open the default browser")
    parser.add_argument("--quiet", action="store_true", help="Suppress request logs")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def main() -> int:
    args = parse_args()
    try:
        repository = Repository(args.repo)
        application = ReviewApplication(repository)
        server = ReviewHTTPServer(("127.0.0.1", args.port), application, quiet=args.quiet)
    except (OSError, RepositoryError) as exc:
        message = exc.message if isinstance(exc, RepositoryError) else str(exc)
        print(f"Could not start Learning Hub review: {message}", file=sys.stderr)
        return 2

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    ready = {"url": url, "port": port, "pid": None}
    print("LEARNING_HUB_READY " + json.dumps(ready, separators=(",", ":")), flush=True)

    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if not args.no_open:
        threading.Timer(0.15, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
