"""A small synchronous Chrome DevTools Protocol client.

Ported from the vendored browser package (0.1.5+aa6, MIT; see LICENSE). Owned by this project (P2).

Self-contained on purpose: the only runtime dependency is a websocket client. Sessions are created
with `Target.attachToTarget(flatten=True)` so many tabs share one socket, and every call is a plain
blocking round trip.

Two changes from the vendored version:
  * the program attaches to the user's real Chrome (D13) and never launches one, so `launch_chrome`
    and its helpers are gone;
  * a CDP command that times out raises `DriverTimeout` (not `CdpError`) and keeps the process alive
    — the vendored `os._exit(3)` path is gone (P2). `Target.targetCreated` events are captured into
    `openers` (targetId -> openerId), which nothing clears, so a tab opened by the job tab can be
    told from one the user opened even after `events` is cleared.
"""

from __future__ import annotations

import itertools
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from websockets.sync.client import connect


class DriverError(RuntimeError):
    """Anything the owned browser driver refuses or cannot do. The fill loop maps it to load_failure."""


class CdpError(DriverError):
    """A CDP method returned an error, or the transport failed."""


class DriverTimeout(CdpError):
    """A CDP command did not return within its timeout. The process stays alive (P2).

    A subclass of CdpError on purpose: the op executor and the settle/observe polls swallow a CdpError
    from an eval issued while the page is mid-navigation (`_safe_eval`), and a short-eval timeout there
    must behave the same. The fill loop still tells the two apart — it catches DriverTimeout (a hung
    browser call → StopRun, exit 3) before DriverError (→ load_failure)."""


class ChromeLaunchError(DriverError):
    """Could not reach a CDP endpoint on the browser we were pointed at."""


class Cdp:
    """One websocket to a browser or page endpoint."""

    def __init__(self, ws_url: str, timeout: float = 30.0, max_size: int = 128 * 1024 * 1024,
                 open_timeout: float | None = None):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ids = itertools.count(1)
        # `open_timeout` is separate because Chrome gates each debugging client behind a user-approval
        # dialog: the handshake waits on a person, not on the network, so it gets more room.
        self._ws = connect(ws_url, max_size=max_size, open_timeout=open_timeout or timeout,
                           close_timeout=5, max_queue=64)
        self.events: deque[dict] = deque(maxlen=400)
        # targetId -> openerId, filled from Target.targetCreated. Never cleared, so a tab opened by the
        # job tab is still identifiable after events.clear() (P2 tab handling; deque above can evict).
        self.openers: dict[str, str | None] = {}

    def _note_event(self, message: dict) -> None:
        self.events.append(message)
        if message.get("method") == "Target.targetCreated":
            info = (message.get("params") or {}).get("targetInfo") or {}
            tid = info.get("targetId")
            if tid:
                self.openers[tid] = info.get("openerId")

    def call(self, method: str, session_id: str | None = None, timeout: float | None = None, **params):
        """Issue a CDP command and return its `result` object."""
        message_id = next(self._ids)
        payload = {"id": message_id, "method": method, "params": params}
        if session_id:
            payload["sessionId"] = session_id
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:  # transport died
            raise CdpError(f"{method}: transport closed ({exc})") from None
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DriverTimeout(f"{method}: timed out after {timeout or self.timeout:.1f}s")
            try:
                raw = self._ws.recv(timeout=remaining)
            except TimeoutError:
                raise DriverTimeout(f"{method}: timed out after {timeout or self.timeout:.1f}s") from None
            except Exception as exc:
                raise CdpError(f"{method}: transport closed ({exc})") from None
            message = json.loads(raw)
            if message.get("id") != message_id:
                if "method" in message:
                    self._note_event(message)
                continue
            if "error" in message:
                error = message["error"]
                raise CdpError(f"{method}: {error.get('message')} ({error.get('code')})")
            return message.get("result", {})

    def evaluate(self, expression: str, session_id: str, *,
                 await_promise: bool = False, timeout: float | None = None):
        """Evaluate JS and return its value. JS exceptions surface as CdpError."""
        result = self.call(
            "Runtime.evaluate",
            session_id=session_id,
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
            userGesture=True,
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            description = (
                (details.get("exception") or {}).get("description")
                or details.get("text")
                or "javascript error"
            )
            raise CdpError(str(description).splitlines()[0][:300])
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------- attach


def attach_chrome(
    url: str,
    timeout: float = 30.0,
    data_dirs: "list[Path] | None" = None,
    open_timeout: float | None = None,
) -> Cdp:
    """Connect to an already-running browser and turn on target discovery.

    A `ws://…` / `wss://…` endpoint is used as it is. An `http://host:port` endpoint is resolved by
    trying three things in order, because a debugging server can be reached in three different shapes:

      1. **`/json/version`** — the classic `--remote-debugging-port` case, which answers with
         `webSocketDebuggerUrl`.
      2. **`DevToolsActivePort`** in the browser's data directory — for Chrome 144+, whose
         `chrome://inspect/#remote-debugging` server is WebSocket-only and answers 404 to every HTTP
         path. The file's two lines are the port and the browser WebSocket path.
      3. **`ws://host:port/devtools/browser`** — the browser endpoint with no target id, used when
         something *is* serving on the port but neither of the first two could name the endpoint.
         This is the path that keeps the toggle working on macOS 26+, where Chrome's data directory is
         protected: `stat` on `DevToolsActivePort` succeeds but `open` raises `EPERM` unless the caller
         has Full Disk Access, so step 2 cannot read a file that is plainly there (live, Chrome 154 on
         Darwin 27, 2026-10-06 — see DISCOVERY.md). Confirmed to upgrade and serve `Browser.getVersion`
         and `Target.*` on that Chrome.

    Step 3 only runs when step 1 proved something is listening, so a port with nothing behind it still
    fails fast with `ChromeLaunchError` instead of hanging on a websocket handshake.

    Chrome asks the user to approve each new debugging client, so the socket is opened with a generous
    `open_timeout`: the handshake sits there until the approval dialog is answered.
    """
    endpoint = url.rstrip("/")
    if endpoint.startswith(("ws://", "wss://")) or not endpoint.startswith("http"):
        cdp = Cdp(endpoint, timeout=timeout, open_timeout=open_timeout)
    else:
        ws_url, serving = _from_json_version(endpoint, timeout)
        if ws_url is None:
            ws_url = _from_active_port(endpoint, data_dirs or [])
        if ws_url is None and serving:
            ws_url = browser_ws(endpoint)
        if ws_url is None:
            raise ChromeLaunchError(
                f"Nothing is listening on {url}, so no browser there has debugging enabled. Either "
                "start Chrome with --remote-debugging-port=9222, or turn the toggle on in "
                "chrome://inspect/#remote-debugging and check that it still says 'Server running at'."
            )
        cdp = Cdp(ws_url, timeout=timeout, open_timeout=open_timeout)
    # Needed so Target.targetCreated events arrive (a tab opened by the page, P2 tab handling).
    cdp.call("Target.setDiscoverTargets", discover=True)
    return cdp


BROWSER_WS_PATH = "/devtools/browser"   # the browser endpoint, with no target id (step 3 above)


def browser_ws(endpoint: str) -> str:
    """`ws://host:port/devtools/browser` for an `http://host:port` endpoint. No I/O."""
    return "ws://" + (urllib.parse.urlparse(endpoint).netloc or "127.0.0.1:9222") + BROWSER_WS_PATH


def _from_json_version(endpoint: str, timeout: float) -> tuple[str | None, bool]:
    """The classic discovery path. Returns (ws_url, serving).

    `serving` says whether anything answered on the port at all — including the 404 that Chrome's
    WebSocket-only `chrome://inspect` server returns to every HTTP path. That is what tells a browser
    with no HTTP API apart from a port with nothing behind it, and it is the only thing that lets
    `attach_chrome` fall back to the browser endpoint (step 3) without risking a hang.
    """
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=timeout) as response:
            return json.load(response)["webSocketDebuggerUrl"], True
    except urllib.error.HTTPError:
        return None, True                              # answered, but serves no HTTP API
    except (urllib.error.URLError, OSError):
        return None, False                             # nothing is listening
    except (KeyError, ValueError):
        return None, True                              # answered with something else than we expect


def _from_active_port(endpoint: str, data_dirs: list[Path]) -> str | None:
    """Resolve the WebSocket endpoint from `DevToolsActivePort`.

    The file is two lines: the port, then the browser endpoint path. Its port has to be the one we were
    pointed at; otherwise it belongs to some other browser that happens to have run on this machine.

    An `OSError` here is not necessarily a missing file: on macOS 26+ Chrome's data directory is
    protected, so `read_text` raises `PermissionError` (EPERM) on a file that `stat` reports perfectly
    well, unless the caller has Full Disk Access. Either way the directory is skipped and
    `attach_chrome` goes on to step 3, which needs no file at all.
    """
    wanted = urllib.parse.urlparse(endpoint).port or 9222
    for directory in data_dirs:
        try:
            lines = (directory / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if len(lines) < 2 or not lines[1].startswith("/devtools/"):
            continue
        try:
            port = int(lines[0].strip())
        except ValueError:
            continue
        if port != wanted:
            continue
        return f"ws://127.0.0.1:{port}{lines[1].strip()}"
    return None
