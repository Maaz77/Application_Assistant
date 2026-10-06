"""`attach_chrome`'s three-step endpoint discovery (cdp.py).

The bug this pins: Chrome's `chrome://inspect/#remote-debugging` server answers 404 to every HTTP path,
and on macOS 26+ `DevToolsActivePort` cannot be read without Full Disk Access — so a browser that was
plainly listening on 9222 was reported as "not running with debugging enabled" (live, Chrome 154 on
Darwin 27, 2026-10-06). Step 3 (`ws://host:port/devtools/browser`) is what fixes it, and these tests
hold the ladder's order and its one guard: step 3 only runs when something answered on the port.

No browser and no network: the HTTP side is a local one-request server, and `Cdp` is replaced so no
websocket is ever opened.
"""
import http.server
import threading

import pytest

from assistant.driver import cdp as cdp_mod
from assistant.driver.cdp import ChromeLaunchError, attach_chrome, browser_ws

pytestmark = pytest.mark.unit


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 404
    body = b""

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    """A server on an ephemeral port. `serve(status, body)` returns its http:// endpoint."""
    servers = []

    def serve(status: int, body: bytes = b""):
        handler = type("H", (_Handler,), {"status": status, "body": body})
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield serve
    for srv in servers:
        srv.shutdown()


@pytest.fixture
def attached(monkeypatch):
    """Record the ws_url `attach_chrome` settles on, without opening a socket."""
    seen = {}

    class _FakeCdp:
        def __init__(self, ws_url, **kw):
            seen["ws_url"] = ws_url

        def call(self, *a, **kw):
            return {}

    monkeypatch.setattr(cdp_mod, "Cdp", _FakeCdp)
    return seen


def test_browser_ws_keeps_host_and_port():
    assert browser_ws("http://127.0.0.1:9222") == "ws://127.0.0.1:9222/devtools/browser"
    assert browser_ws("http://localhost:9333") == "ws://localhost:9333/devtools/browser"


def test_json_version_404_still_counts_as_serving(http_server):
    """The guard for step 3: a 404 is a browser with no HTTP API, not an absent browser."""
    assert cdp_mod._from_json_version(http_server(404), 5) == (None, True)


def test_nothing_listening_is_not_serving():
    ws_url, serving = cdp_mod._from_json_version("http://127.0.0.1:1", 2)
    assert (ws_url, serving) == (None, False)


def test_step_1_wins_when_json_version_answers(http_server, attached):
    endpoint = http_server(200, b'{"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/abc"}')
    attach_chrome(endpoint, timeout=5, data_dirs=[])
    assert attached["ws_url"] == "ws://127.0.0.1:1/devtools/browser/abc"


def test_step_2_wins_over_step_3_when_the_file_can_be_read(http_server, attached, tmp_path):
    """DevToolsActivePort names the exact browser id, so a readable file still outranks step 3."""
    endpoint = http_server(404)
    port = endpoint.rsplit(":", 1)[1]
    (tmp_path / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/from-file\n")
    attach_chrome(endpoint, timeout=5, data_dirs=[tmp_path])
    assert attached["ws_url"] == f"ws://127.0.0.1:{port}/devtools/browser/from-file"


def test_step_3_rescues_an_unreadable_devtoolsactiveport(http_server, attached, tmp_path, monkeypatch):
    """The live bug: the port serves, the file exists, and reading it raises EPERM."""
    endpoint = http_server(404)
    active = tmp_path / "DevToolsActivePort"
    active.write_text("1\n/devtools/browser/unreadable\n")
    real_read = type(active).read_text

    def deny(self, *a, **kw):
        if self.name == "DevToolsActivePort":
            raise PermissionError(1, "Operation not permitted")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(type(active), "read_text", deny)
    attach_chrome(endpoint, timeout=5, data_dirs=[tmp_path])
    assert attached["ws_url"] == browser_ws(endpoint)


def test_step_3_rescues_a_missing_devtoolsactiveport(http_server, attached):
    endpoint = http_server(404)
    attach_chrome(endpoint, timeout=5, data_dirs=[])
    assert attached["ws_url"] == browser_ws(endpoint)


def test_a_dead_port_fails_fast_instead_of_guessing(attached):
    """Step 3 must not fire when nothing answered, or a dead port would hang on a handshake."""
    with pytest.raises(ChromeLaunchError, match="Nothing is listening"):
        attach_chrome("http://127.0.0.1:1", timeout=2, data_dirs=[])
    assert "ws_url" not in attached


def test_a_ws_endpoint_is_used_as_it_is(attached):
    attach_chrome("ws://127.0.0.1:9222/devtools/browser/given", timeout=5)
    assert attached["ws_url"] == "ws://127.0.0.1:9222/devtools/browser/given"
