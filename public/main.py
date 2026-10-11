"""
public/main.py
----------------
Development server entry point - this is the project's entry point, run
directly:

    python public/main.py

or through the CLI, which just calls run() below:

    python console.py serve

Host/port come from .env (APP_HOST/APP_PORT), with run()'s arguments (and
the CLI's --host/--port) overriding them for a one-off run.
"""

import os
import sys
from http.server import HTTPServer

# make "core" importable whether this is run directly (python public/main.py,
# which puts this file's own directory - public/ - on sys.path, not the
# project root) or imported from elsewhere (python console.py serve)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.http.http.Server import Server
from core.services.EnvService import EnvService


def run(host=None, port=None):
    env = EnvService()
    host = host or env.get("APP_HOST", "127.0.0.1")
    port = port or env.get_int("APP_PORT", 8000)

    server = HTTPServer((host, port), Server)
    print(f"Server started at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
