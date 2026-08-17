#!/usr/bin/env python3
"""Launch PDF Workbench and open it in the default browser.

    python run_app.py                 # start on the first free port from 8000
    python run_app.py --port 9000     # pin a port
    python run_app.py --no-browser    # just serve
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser


def find_free_port(preferred: int, host: str = "127.0.0.1", tries: int = 25) -> int:
    for offset in range(tries):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port found in {preferred}–{preferred + tries}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("Missing dependencies. Run:  pip install -r requirements.txt")
        return 1

    from src import APP_NAME, __version__, config

    port = args.port if args.reload else find_free_port(args.port, args.host)
    url = f"http://{args.host}:{port}"

    print("=" * 62)
    print(f"  {APP_NAME} {__version__}")
    print("=" * 62)
    print(f"  Serving   {url}")
    print(f"  Workspace {config.WORKSPACE}")

    try:
        from src.ocr import registry

        engines = [e for e in registry.list_engines() if e.available and e.name != "native"]
        if engines:
            print(f"  OCR       {', '.join(e.label for e in engines)}")
        else:
            print("  OCR       none installed — scanned PDFs cannot be read yet")
            print("            quick fix: pip install rapidocr-onnxruntime")
    except Exception as exc:  # noqa: BLE001 - never block startup on a probe
        print(f"  OCR       probe failed: {exc}")

    print("=" * 62)
    print("  Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.2), webbrowser.open(url)),
            daemon=True,
        ).start()

    uvicorn.run(
        "src.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
