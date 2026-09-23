"""Throwaway Chrome and the local fixture server (§9). Never touches the user's browser."""
from __future__ import annotations

import http.server
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
POSTS_LOG = FIXTURES / "posts.log"
CDP_PORT = 9223
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _trip(self, method: str) -> None:
        with open(POSTS_LOG, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {method} {self.path}\n")

    def do_GET(self):
        if self.path.startswith("/submit"):
            self._trip("GET")
            self.send_response(200); self.end_headers(); self.wfile.write(b"submitted")
            return
        super().do_GET()

    def do_POST(self):
        self._trip("POST")
        self.send_response(200); self.end_headers(); self.wfile.write(b"submitted")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FixtureServer:
    """http.server on 127.0.0.1 serving tests/fixtures; every POST or /submit request → posts.log."""

    def __init__(self, port: int | None = None):
        self.port = port or _free_port()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port),
                                                     partial(_Handler, directory=str(FIXTURES)))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def __enter__(self):
        POSTS_LOG.write_text("")
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()

    @staticmethod
    def posts() -> list[str]:
        return [l for l in POSTS_LOG.read_text().splitlines() if l.strip()] if POSTS_LOG.exists() else []


class ThrowawayChrome:
    def __init__(self, chrome: str, port: int = CDP_PORT):
        self.chrome, self.port = chrome, port
        self.profile = Path(tempfile.mkdtemp(prefix="aa-chrome-"))
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [self.chrome, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                self.tabs()
                return self
            except OSError:
                time.sleep(0.2)
        self.__exit__()
        raise RuntimeError("throwaway Chrome did not expose CDP")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def close_tab(self, target_id: str) -> None:
        """Test cleanup only (the throwaway Chrome serves the HTTP endpoints; the user's Chrome may not)."""
        urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{target_id}", timeout=2).read()

    def tabs(self) -> list[dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:
            return [t for t in json.load(r) if t.get("type") == "page"]
