"""gateway.py (P1 T7): the queue, the one retry layer, the timeouts, the logging and the two clean stops.

The HTTP-level tests run against a real local server (`http.server` on a free port), so the retry rule is exercised
through httpx and a real `Retry-After` header rather than through a stub that only pretends to be one. Nothing here
reaches a real provider.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from assistant import gateway as G
from assistant import inference_log

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ a local server


class _Server:
    """Answers each request from a script of (status, body, headers, hold) and records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[dict] = []
        self.starts: list[float] = []
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                with outer._lock:
                    outer.live += 1
                    outer.peak = max(outer.peak, outer.live)
                    outer.starts.append(time.monotonic())
                    status, payload, headers, hold = outer.script[min(len(outer.seen), len(outer.script) - 1)]
                raw_in = self.rfile.read(int(self.headers.get("content-length") or 0))
                with outer._lock:
                    outer.seen.append(json.loads(raw_in or b"{}"))
                if hold:
                    time.sleep(hold)
                raw = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("content-type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                with outer._lock:
                    outer.live -= 1

            def log_message(self, *a):
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_port}/v1/systemone"
        # The handler ignores the path, so the same server serves both senders: `url` goes to httpx and
        # `chat_url` to the OpenAI SDK (gateway._post dispatches on the /chat/completions suffix).
        self.chat_url = f"http://127.0.0.1:{self.http.server_port}/v1/chat/completions"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()


def step(status=200, body=None, headers=None, hold=0.0):
    return (status, {"answers": {}} if body is None else body, headers, hold)


@pytest.fixture
def server():
    made = []

    def make(*script):
        s = _Server(script or [step()])
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def gw(*, max_in_flight=1, min_interval_s=0.0, max_attempts=3, **kw) -> G.Gateway:
    return G.Gateway(limits=SimpleNamespace(max_in_flight=max_in_flight, min_interval_s=min_interval_s,
                                            max_attempts=max_attempts), **kw)


def send(gateway, url, kind=G.JEV, body=None, **kw):
    return gateway.send(kind, url, body or {"model": "m", "state": "s", "questions": {}}, {}, **kw)


class _Clock:
    """A fake clock: `sleep` records what it was asked to wait for and advances the clock instead of waiting.

    Pacing is a decision — "wait out what is left of the interval, then start" — so the tests below assert what
    the Gateway asked for. Timing it on the real clock measured `time.sleep`'s precision instead, which made it
    flaky against any threshold tight enough to prove the interval was not skipped.
    """

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# ------------------------------------------------------------------ the queue


def test_only_one_request_is_in_flight_at_a_time(server):
    """P1 T2: limits.max_in_flight. The server itself counts how many arrived at once, so this proves the queue
    rather than the counter that reports it."""
    s = server(step(hold=0.05))
    gateway = gw()
    threads = [threading.Thread(target=send, args=(gateway, s.url)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.seen) == 6 and s.peak == 1 and gateway.run.in_flight == 1


def test_requests_start_at_least_the_minimum_interval_apart():
    """P1 T2: limits.min_interval_s. On a fake clock, so this asserts the wait the Gateway decides on rather than
    the precision of the host's sleep. Skipping the interval leaves `slept` empty and every start at 0.0."""
    clock = _Clock()
    starts = []

    def post(url, body, headers, timeout):
        starts.append(clock.now)
        return 200, {"answers": {}}
    gateway = gw(min_interval_s=0.25, post=post, sleep=clock.sleep, monotonic=clock.monotonic)
    for _ in range(4):
        send(gateway, "http://127.0.0.1:1/v1/systemone")
    assert clock.slept == [0.25, 0.25, 0.25]      # the whole interval, before every request but the first
    assert starts == [0.0, 0.25, 0.5, 0.75]       # and waited out before the request goes out, not after


def test_a_slow_request_does_not_add_its_duration_to_the_interval():
    """The interval is between request STARTS: a request that took a second does not then wait another 0.25 s.
    On the same fake clock, where a slow request is one that advances the clock past the interval itself."""
    clock = _Clock()
    starts = []

    def post(url, body, headers, timeout):
        starts.append(clock.now)
        clock.now += 0.2                     # the request itself takes twice the interval
        return 200, {"answers": {}}
    gateway = gw(min_interval_s=0.1, post=post, sleep=clock.sleep, monotonic=clock.monotonic)
    for _ in range(3):
        send(gateway, "http://127.0.0.1:1/v1/systemone")
    assert clock.slept == []                 # each request already outlasted the interval
    assert starts == [0.0, 0.2, 0.4]         # so the starts are its duration apart, not duration + interval


# ------------------------------------------------------------------ the one retry layer


def test_a_retryable_status_is_retried_up_to_max_attempts(server):
    for status in (429, 500, 503, 529):
        s = server(step(status, {"error": "busy"}))
        slept = []
        out = send(gw(sleep=slept.append), s.url)
        assert not out.ok and out.attempts == 3 and len(s.seen) == 3 and slept == list(G.BACKOFF)


def test_a_retry_succeeds_and_the_answer_comes_back(server):
    s = server(step(503, {"error": "busy"}), step(200, {"answers": {"x": 1}}))
    out = send(gw(sleep=lambda _: None), s.url)
    assert out.ok and out.attempts == 2 and out.body == {"answers": {"x": 1}}


def test_retry_after_is_waited_out_when_it_is_short_enough(server):
    s = server(step(429, {"error": "slow down"}, {"retry-after": "7"}), step(200))
    slept = []
    out = send(gw(sleep=slept.append), s.url)
    assert out.ok and slept == [7.0]


def test_a_retry_after_over_the_cap_falls_back_to_the_backoff(server):
    s = server(step(429, {"error": "slow down"}, {"retry-after": "600"}), step(200))
    slept = []
    send(gw(sleep=slept.append), s.url)
    assert slept == [G.BACKOFF[0]]           # 600 s is the provider saying stop, not wait


def test_another_4xx_is_never_retried(server):
    for status in (400, 404, 422):
        s = server(step(status, {"error": "no"}))
        out = send(gw(sleep=lambda _: None), s.url)
        assert not out.ok and out.attempts == 1 and len(s.seen) == 1


def test_a_timeout_counts_as_a_retryable_failure(server):
    s = server(step(hold=0.4))
    out = send(gw(sleep=lambda _: None), s.url, timeout=0.05)
    assert not out.ok and out.status == 0 and out.attempts == 3


def test_an_unreachable_server_is_retried_then_reported():
    dead = "http://127.0.0.1:9/v1/systemone"          # discard port: nothing listens
    out = send(gw(sleep=lambda _: None), dead)
    assert not out.ok and out.status == 0 and out.attempts == 3


def test_a_caller_with_another_model_to_try_gets_one_attempt(server):
    """A rotation's next model is cheaper than retrying a rate-limited model, so `attempts=1` caps this request."""
    s = server(step(429, {"error": "busy"}))
    out = send(gw(sleep=lambda _: None), s.url, attempts=1)
    assert not out.ok and out.attempts == 1 and len(s.seen) == 1


def test_the_attempt_cap_never_raises_the_configured_limit(server):
    s = server(step(503, {"error": "busy"}))
    out = send(gw(max_attempts=2, sleep=lambda _: None), s.url, attempts=9)
    assert out.attempts == 2


# ------------------------------------------------------------------ logging (§6.4, D4)


def _entries(tmp_path, name):
    return json.loads((tmp_path / "_run" / name).read_text())


@pytest.fixture
def logs(tmp_path):
    inference_log.start_run(tmp_path)
    yield tmp_path
    inference_log.start_run(None)


def test_every_attempt_is_logged_not_only_the_last(server, logs):
    s = server(step(503, {"error": "busy"}), step(503, {"error": "busy"}), step(200, {"answers": {"x": 1}}))
    send(gw(sleep=lambda _: None), s.url)
    entries = _entries(logs, "jev_inference_logs.json")
    assert len(entries) == 3 and entries[-1]["Response"] == {"answers": {"x": 1}}
    assert entries[0]["Response"] == {"error": "busy"}


def test_a_chat_request_lands_in_the_llm_log_and_a_system_one_request_in_the_other(server, logs):
    s = server(step(200, {"choices": [{"message": {"content": "hi"}}]}))
    gateway = gw()
    send(gateway, s.url, G.CHAT, body={"model": "m", "messages": [{"role": "user", "content": "q"}]})
    send(gateway, s.url, G.JEV)
    assert len(_entries(logs, "llm_inference_logs.json")) == 1
    assert len(_entries(logs, "jev_inference_logs.json")) == 1


def test_a_request_that_never_got_a_body_says_so(logs):
    """§6.2/§6.3: with no response body the entry reads "<no response body: …>", not our own error object."""
    send(gw(sleep=lambda _: None), "http://127.0.0.1:9/v1/systemone")
    assert _entries(logs, "jev_inference_logs.json")[0]["Response"].startswith("<no response body:")


def test_no_key_reaches_a_log(server, logs):
    s = server(step())
    gw().send(G.JEV, s.url, {"model": "m", "state": "s", "questions": {}},
              {"Authorization": "Bearer sk-secret-value"})
    assert "sk-secret-value" not in (logs / "_run" / "jev_inference_logs.json").read_text()


# ------------------------------------------------------------------ counters


def test_counters_count_requests_attempts_and_failures(server):
    """One request, two attempts, no failure: a retried request is still one request (the breaker counts requests,
    not attempts). Cost is not counted at all since 2026-10-07 — both servers are local and free."""
    s = server(step(503, {"error": "busy"}), step(200, {"answers": {}}))
    gateway = gw(sleep=lambda _: None)
    send(gateway, s.url)
    assert (gateway.run.requests, gateway.run.attempts, gateway.run.failures) == (1, 2, 0)
    assert not hasattr(gateway.run, "cost") and "$" not in gateway.run.row()


def test_per_job_counters_reset_while_the_runs_keep_counting(server):
    s = server(step())
    gateway = gw()
    send(gateway, s.url)
    job = gateway.start_job()
    send(gateway, s.url)
    assert job.requests == 1 and gateway.run.requests == 2


# ------------------------------------------------------------------ the breaker and the caps (T3)


def test_three_failed_requests_in_a_row_are_a_provider_outage(server):
    s = server(step(503, {"error": "down"}))
    gateway = gw(sleep=lambda _: None)
    send(gateway, s.url)
    send(gateway, s.url)
    with pytest.raises(G.ProviderOutage, match="3 requests in a row"):
        send(gateway, s.url)


def test_five_of_the_last_ten_are_a_provider_outage():
    gateway = gw(sleep=lambda _: None)
    for verdict in [True, False, True, False, True, False, True, False, False]:
        gateway.note_failure(verdict)
    with pytest.raises(G.ProviderOutage, match="5 of the last 10"):
        gateway.note_failure(True)


def test_a_run_of_successes_does_not_trip_the_breaker():
    gateway = gw()
    for _ in range(30):
        gateway.note_failure(False)
    assert gateway.tripped is None


def test_the_outage_names_the_route_and_the_status(server):
    s = server(step(503, {"error": "down"}))
    gateway = gw(sleep=lambda _: None)
    send(gateway, s.url)
    send(gateway, s.url)
    with pytest.raises(G.ProviderOutage, match="HTTP 503"):
        send(gateway, s.url)


def test_a_slow_server_times_out_and_is_not_called_unreachable(server):
    """A server that answers too slowly is not an unreachable one. Every status 0 was labelled "unreachable", which
    is how the run of 2026-10-08 reported "freellmapi unreachable" while the router was up (gateway._no_answer)."""
    # One attempt per request: a retry ladder would leave nine held requests queued on a server that answers one
    # at a time, and the connection refused by that backlog is a different failure from the one under test.
    s = server(step(hold=0.3))
    gateway = gw(max_attempts=1, sleep=lambda _: None)
    send(gateway, s.url, timeout=0.05)
    send(gateway, s.url, timeout=0.05)
    with pytest.raises(G.ProviderOutage, match="timed out"):
        send(gateway, s.url, timeout=0.05)


def test_a_server_that_is_really_unreachable_still_says_so():
    dead = "http://127.0.0.1:9/v1/systemone"          # discard port: nothing listens
    gateway = gw(sleep=lambda _: None)
    send(gateway, dead)
    send(gateway, dead)
    with pytest.raises(G.ProviderOutage, match="unreachable"):
        send(gateway, dead)


def test_no_key_or_no_credit_stops_the_run_and_is_never_retried(server):
    for status in (401, 402, 403):
        s = server(step(status, {"error": {"message": "nope"}}))
        with pytest.raises(G.CreditOrKey) as ei:
            send(gw(sleep=lambda _: None), s.url)
        assert f"HTTP {status}" in str(ei.value) and len(s.seen) == 1


def test_a_stop_is_sticky_and_comes_back_at_every_later_entry_point(server):
    """A stop can be raised inside the package's own thread, where a broad `except Exception` turns it into a
    string. The Gateway keeps it so `Jev.call` can raise it on the run's thread instead (T3)."""
    s = server(step(402, {"error": {"message": "no credit"}}))
    gateway = gw(sleep=lambda _: None)
    with pytest.raises(G.CreditOrKey):
        send(gateway, s.url)
    with pytest.raises(G.CreditOrKey):
        gateway.check()
    with pytest.raises(G.CreditOrKey):
        send(gateway, s.url)
    assert len(s.seen) == 1                  # nothing was sent after the stop


def test_every_stop_is_a_stop_run_so_the_run_writes_no_record():
    """cli.run already turns StopRun into exit 3 with no folder move, no tracker write and no job.md note. Being a
    subclass is what keeps the two stops out of the NeedsAttention paths, which would record the job."""
    from assistant.blockers import NeedsAttention, StopRun
    for cls in (G.ProviderOutage, G.CreditOrKey):
        assert issubclass(cls, StopRun) and issubclass(cls, G.GatewayStop)
        assert not issubclass(cls, NeedsAttention)


# ------------------------------------------------------------------ the slot the fallback needs


def test_the_slot_is_free_again_as_soon_as_a_request_is_done(server):
    """max_in_flight = 1 plus a fallback that itself sends would self-deadlock if the slot outlived the request.
    It does not: `send` releases it before returning, so a second send from the same caller goes straight out (T4)."""
    s = server(step(503, {"error": "busy"}), step(200))
    gateway = gw(sleep=lambda _: None)
    send(gateway, s.url)
    assert gateway._live == 0
    assert send(gateway, s.url).ok and len(s.seen) == 3


# ------------------------------------------------------------------ wiring


def test_for_config_takes_its_limits_and_both_timeouts_from_the_config():
    from assistant import config
    cfg = config.load()
    gateway = G.for_config(cfg)
    assert gateway.limits is cfg.limits
    # Both routes are on this Mac and neither answers in a data-centre's milliseconds, so neither keeps the
    # hard-coded default: the chat timeout is models.freellmapi.timeout (a 45 s constant in llm_inference.py made a
    # slow free tier look like an outage, 2026-10-08) and the System One one is models.local.timeout.
    assert gateway.timeouts[G.CHAT] == cfg.models.freellmapi.timeout
    assert gateway.timeouts[G.JEV] == cfg.models.local.timeout


def test_a_sender_without_a_gateway_is_refused():
    G.use(None)
    with pytest.raises(RuntimeError, match="may not bypass"):
        G.required()


def test_a_rotation_handing_over_is_not_a_failed_request():
    """One model rate-limited while another answers is the rotation working, not a provider outage. Vercel allows
    5 requests a minute per model, so this alternation is the normal case, and counting each handover as a failure
    would trip "5 of the last 10" while every page was answered (same rule as the chat fallback's, T4)."""
    from assistant import llm_inference as L
    gateway = gw(sleep=lambda _: None)
    G.use(gateway)
    try:
        def post(url, body, headers, timeout):
            if body["model"] == "busy":
                return 429, {"error": {"message": "rate-limited upstream"}}
            return 200, {"choices": [{"message": {"content": json.dumps({"questions": []})}}]}
        gateway.post = post
        for _ in range(12):
            L.call_engine(key="k", models=["busy", "free"], system="s", user={},
                          url="https://x/v1/chat/completions")
        assert gateway.tripped is None and gateway.run.failures == 0
    finally:
        G.use(None)


def test_a_rotation_where_no_model_answers_is_one_failed_request():
    from assistant import llm_inference as L
    gateway = gw(sleep=lambda _: None)
    G.use(gateway)
    try:
        gateway.post = lambda *a: (429, {"error": {"message": "rate-limited"}})
        for _ in range(2):
            with pytest.raises(L.LLMInferenceError):
                L.call_engine(key="k", models=["a", "b"], system="s", user={},
                              url="https://x/v1/chat/completions")
        assert gateway.run.failures == 2          # two calls, not four models
        with pytest.raises(G.ProviderOutage):
            L.call_engine(key="k", models=["a", "b"], system="s", user={},
                          url="https://x/v1/chat/completions")
    finally:
        G.use(None)


# ------------------------------------------------------------------ the OpenAI SDK transport (2026-10-07)
#
# Every other test in this file injects `post=`, so none of them ever runs the real chat sender. These do: they
# drive `gateway._openai_post` against the local server through `Gateway.send`, which is the only way a chat
# request leaves the program.


def chat_body(**kw):
    return {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16, **kw}


def chat_send(gateway, url, **kw):
    return gateway.send(G.CHAT, url, chat_body(), {"Authorization": "Bearer k"}, model="m", **kw)


def test_the_sdk_sender_passes_the_wire_json_through_untouched(server):
    """The raw body, not the SDK's typed model: `_routed_via` is a field the SDK knows nothing about, and
    `inference_log._provider` reads it. A `parse().model_dump()` would also add the SDK's own unset fields."""
    answer = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 3, "completion_tokens": 1},
              "_routed_via": {"platform": "groq", "model": "qwen/qwen3.8-27b"}}
    s = server(step(200, answer))
    out = chat_send(gw(), s.chat_url)
    assert out.ok and out.status == 200
    assert out.body["_routed_via"] == {"platform": "groq", "model": "qwen/qwen3.8-27b"}
    assert out.body["choices"][0]["message"]["content"] == "ok"
    assert "refusal" not in out.body["choices"][0]["message"]    # the SDK model would have added it
    assert s.seen[0]["model"] == "m" and s.seen[0]["max_tokens"] == 16


def test_the_sdk_sender_does_not_retry_on_its_own(server):
    """max_retries=0. The SDK's default is 2, which would make 3 HTTP requests inside each Gateway attempt — the
    nesting this module exists to remove. One Gateway attempt must be exactly one HTTP request."""
    s = server(step(500, {"error": "boom"}))
    out = chat_send(gw(sleep=lambda _: None), s.chat_url)
    assert not out.ok and out.attempts == 3        # the Gateway's three, and no more
    assert len(s.seen) == 3                       # exactly one HTTP request per attempt


def test_the_sdk_sender_honours_retry_after(server):
    """The SDK raises instead of returning, so `Retry-After` has to be carried off the exception's response or the
    Gateway silently falls back to the fixed BACKOFF ladder."""
    waits = []
    s = server(step(429, {"error": "slow down"}, {"retry-after": "7"}), step(200, {"choices": []}))
    out = chat_send(gw(sleep=waits.append), s.chat_url)
    assert out.ok and waits == [7.0]


def test_the_sdk_sender_maps_a_rejected_key_to_credit_or_key(server):
    s = server(step(401, {"error": {"message": "Invalid API key"}}))
    with pytest.raises(G.CreditOrKey, match="HTTP 401"):
        chat_send(gw(), s.chat_url)
    assert len(s.seen) == 1                       # never retried


def test_the_sdk_sender_reports_a_non_json_body(server):
    """A proxy answering HTML must not crash the sender; the body shape stays the same as httpx's."""
    s = server(step(502, None))
    out = chat_send(gw(sleep=lambda _: None), s.chat_url)
    assert not out.ok and out.status == 502


def test_the_sdk_sender_reports_no_connection_as_status_zero(server):
    """An SDK APIConnectionError becomes status 0, exactly as a transport failure does on the httpx sender, so
    `_retryable` and the log do not depend on which sender ran."""
    s = server(step(200))
    port = s.http.server_port
    s.close()
    out = chat_send(gw(sleep=lambda _: None), f"http://127.0.0.1:{port}/v1/chat/completions")
    assert out.status == 0 and not out.ok and out.attempts == 3


def test_the_systemone_path_stays_on_httpx(server):
    """Kev's /v1/systemone is not an OpenAI endpoint, so it must not go near the SDK. Dispatch is by path."""
    s = server(step(200, {"answers": {}}))
    out = send(gw(), s.url)
    assert out.ok and out.body == {"answers": {}}
    assert G._post is not G._openai_post
