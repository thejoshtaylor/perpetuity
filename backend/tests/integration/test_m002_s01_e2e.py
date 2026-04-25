"""M002 / S01 / T06 — End-to-end acceptance test.

Stitches every prior task in the slice together against the **real compose
stack** (NOT TestClient). Demonstrates the four headline guarantees of S01:

  1. A signed-up user can `POST /api/v1/sessions` and get a `session_id`.
  2. Opening `wss://.../api/v1/ws/terminal/<sid>` with the cookie produces an
     `attach` frame and round-trips an `echo hello` through the tmux pane.
  3. Restarting the orchestrator (`docker compose restart orchestrator`) does
     NOT kill the user's shell. After the restart, reattaching to the SAME
     `session_id` yields the prior scrollback AND the same shell PID
     (`echo $$` returns the same number) — proof tmux is the pty owner.
  4. The orchestrator + backend logs captured during the run contain
     ZERO occurrences of the seeded user's email or full_name. Per the
     M002 observability appendix, all log lines that include identifiers
     emit UUIDs only.

How to run:

    docker compose build orchestrator backend
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v

If `SKIP_INTEGRATION=1`, the docker socket isn't reachable, or required
images are missing, the test skips cleanly so unit-only runs are unaffected.

The `backend_url` fixture (in `conftest.py`) spawns a fresh sibling
backend container on `perpetuity_default` with a published host port —
that way the test reaches the backend over HTTP, but the backend reaches
the orchestrator over the internal compose DNS as `http://orchestrator:8001`.
This is the key shape that lets `docker compose restart orchestrator`
actually break (and then re-establish) the live connection.

Failure modes embedded in the assertions:
  - step 9: `orchestrator did not become healthy within 30s after restart`
  - step 12: `shell PID changed across orchestrator restart — tmux durability broken`
  - step 14 (log sweep): `seeded email/full_name leaked to logs`
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import time
import uuid

import httpx
import pytest
from httpx_ws import aconnect_ws

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


pytestmark = [pytest.mark.e2e]


# ----- helpers -----------------------------------------------------------


def _b64enc(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


def _http_to_ws(http_base: str) -> str:
    if http_base.startswith("https://"):
        return "wss://" + http_base[len("https://") :]
    if http_base.startswith("http://"):
        return "ws://" + http_base[len("http://") :]
    return "ws://" + http_base


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so substring matches survive a real PTY.

    The workspace image runs an interactive bash; tmux sends color codes
    and cursor moves alongside the literal `hello` we're hunting for. The
    tests assert on the *plain text*, not the formatted stream.
    """
    # Strip CSI sequences (ESC [ ... letter) and OSC sequences (ESC ] ... BEL/ST).
    csi = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    osc = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
    return osc.sub("", csi.sub("", text))


async def _drain_data(
    ws: object, *, timeout_s: float, until_substring: str | None = None
) -> str:
    """Read frames until `until_substring` shows up in decoded data, or timeout.

    Returns the accumulated decoded-and-de-ansi'd stdout. Used both for
    "wait for `hello`" and "wait for the PID readback" — the same shape.
    """
    deadline = time.monotonic() + timeout_s
    accumulated_raw = b""
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            text = await asyncio.wait_for(ws.receive_text(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        frame = json.loads(text)
        if frame.get("type") == "data":
            accumulated_raw += _b64dec(frame["bytes"])
            decoded_plain = _strip_ansi(
                accumulated_raw.decode("utf-8", errors="replace")
            )
            if until_substring is not None and until_substring in decoded_plain:
                return decoded_plain
        elif frame.get("type") == "exit":
            break
    return _strip_ansi(accumulated_raw.decode("utf-8", errors="replace"))


def _input_frame(payload: str) -> str:
    return json.dumps({"type": "input", "bytes": _b64enc(payload)})


def _compose_restart(service: str, *, timeout_s: int = 60) -> None:
    subprocess.run(
        ["docker", "compose", "restart", service],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=timeout_s,
    )


def _wait_orch_healthy(*, timeout_s: float = 30.0) -> None:
    """Poll `docker compose ps orchestrator` until Health=healthy or timeout.

    The orchestrator is only reachable from inside the network, so we can't
    just curl localhost. Compose's healthcheck output is the source of truth.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = subprocess.run(
            ["docker", "compose", "ps", "--format",
             "{{.Service}}\t{{.Health}}", "orchestrator"],
            check=False, capture_output=True, text=True, cwd=REPO_ROOT, timeout=10,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[0] == "orchestrator" and parts[1] == "healthy":
                return
        time.sleep(0.5)
    raise AssertionError(
        f"step 9: orchestrator did not become healthy within {int(timeout_s)}s"
    )


def _signup_login(
    base_url: str, *, email: str, password: str, full_name: str
) -> httpx.Cookies:
    """Hit /auth/signup then /auth/login. Returns the session cookie jar.

    We always re-login after signup so the cookie jar we return is the one
    set by /login (signup *does* set the cookie, but isolating login keeps
    the test independent of any future change to signup's response shape).
    """
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": password, "full_name": full_name},
        )
        assert r.status_code == 200, f"step 2 (signup): {r.status_code} {r.text}"
        c.cookies.clear()
        r = c.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"step 2 (login): {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _personal_team_id(base_url: str, cookies: httpx.Cookies) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/teams/")
        assert r.status_code == 200, f"step 2 (teams list): {r.status_code} {r.text}"
        rows = r.json()["data"]
    personal = next((t for t in rows if t["is_personal"]), None)
    assert personal is not None, f"step 2: no personal team in {rows!r}"
    return personal["id"]


def _create_session(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> str:
    with httpx.Client(base_url=base_url, timeout=60.0, cookies=cookies) as c:
        r = c.post("/api/v1/sessions", json={"team_id": team_id})
        assert r.status_code == 200, (
            f"step 2 (create session): {r.status_code} {r.text}"
        )
        return r.json()["session_id"]


def _delete_session(
    base_url: str, cookies: httpx.Cookies, session_id: str
) -> int:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.delete(f"/api/v1/sessions/{session_id}")
        return r.status_code


def _capture_compose_logs(*services: str) -> str:
    """Return the concatenated `docker compose logs` for the named services."""
    r = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "--timestamps", *services],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    return (r.stdout or "") + (r.stderr or "")


# ----- the test ----------------------------------------------------------


def test_m002_s01_full_e2e(backend_url: str) -> None:  # noqa: PLR0915
    """Full slice acceptance: signup → session → echo hello → orchestrator
    restart → reconnect with same PID and scrollback → delete → log sweep.
    """
    suite_started = time.time()

    # ----- step 2: signup + login + create session -----------------------
    suffix = uuid.uuid4().hex[:8]
    # Use `example.com` (RFC 2606 reserved-for-test domain) — `email_validator`
    # rejects `.local`/`.localhost` as special-use TLDs.
    email = f"m002-s01-e2e-{suffix}@example.com"
    password = "Sup3rs3cret-test"
    full_name = f"E2E User {suffix}"

    cookies = _signup_login(
        backend_url, email=email, password=password, full_name=full_name
    )
    team_id = _personal_team_id(backend_url, cookies)
    session_id = _create_session(backend_url, cookies, team_id)
    assert uuid.UUID(session_id), f"step 2: bad session_id {session_id!r}"

    ws_base = _http_to_ws(backend_url)
    ws_url = f"{ws_base}/api/v1/ws/terminal/{session_id}"
    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies.items())
    assert cookie_header, "step 2: no cookies captured from login"

    async def _phase_one() -> str:
        """Steps 3–7: attach, echo hello, capture pid_before."""
        async with aconnect_ws(
            ws_url, headers={"Cookie": cookie_header}
        ) as ws:
            # step 4: first frame is `attach`.
            first_text = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            first_frame = json.loads(first_text)
            assert first_frame["type"] == "attach", (
                f"step 4: expected attach frame, got {first_frame!r}"
            )
            # Fresh session — scrollback may be empty or carry the shell prompt.
            initial_scrollback = _strip_ansi(
                _b64dec(first_frame["scrollback"]).decode("utf-8", errors="replace")
            )
            # step 5+6: send `echo hello\n`, wait for `hello` in data frames.
            await ws.send_text(_input_frame("echo hello\n"))
            seen = await _drain_data(
                ws, timeout_s=10.0, until_substring="hello"
            )
            assert "hello" in seen, (
                f"step 6: did not see 'hello' in WS data within 10s; "
                f"saw={seen!r} initial_scrollback={initial_scrollback!r}"
            )
            # step 7: capture shell PID via `echo $$`.
            await ws.send_text(_input_frame("echo $$\n"))
            # The reply is "<digits>\r\n". Wait for any digit-run on its own
            # line that is NOT part of the prompt's previous output.
            pid_buffer = await _drain_data(ws, timeout_s=10.0, until_substring=None)
            # `echo $$` produces the pid as a bare number on a line. Find any
            # standalone digit run.
            digits = re.findall(r"(?<!\d)(\d{2,7})(?!\d)", pid_buffer)
            assert digits, f"step 7: no PID digits in echo $$ output: {pid_buffer!r}"
            # The most recent number is the shell pid.
            pid_before = digits[-1]
            # step 8: explicit close (context-manager exit also closes).
            return pid_before

    pid_before = asyncio.run(_phase_one())
    assert pid_before, "step 7: empty pid_before"

    # ----- step 9: restart orchestrator + wait healthy -------------------
    _compose_restart("orchestrator")
    _wait_orch_healthy(timeout_s=30.0)

    async def _phase_two() -> tuple[str, str, str]:
        """Steps 10–13: reattach, assert hello-in-scrollback + same PID +
        new echo round-trip."""
        async with aconnect_ws(
            ws_url, headers={"Cookie": cookie_header}
        ) as ws:
            # step 11: first frame is attach with scrollback.
            first_text = await asyncio.wait_for(ws.receive_text(), timeout=20.0)
            first_frame = json.loads(first_text)
            assert first_frame["type"] == "attach", (
                f"step 11: expected attach frame post-restart, got {first_frame!r}"
            )
            scrollback_after = _strip_ansi(
                _b64dec(first_frame["scrollback"]).decode("utf-8", errors="replace")
            )
            # step 12: same shell PID — proves tmux durability.
            await ws.send_text(_input_frame("echo $$\n"))
            pid_buffer = await _drain_data(
                ws, timeout_s=10.0, until_substring=pid_before
            )
            # step 13: echo world\n round-trip on the same shell.
            await ws.send_text(_input_frame("echo world\n"))
            world_buffer = await _drain_data(
                ws, timeout_s=10.0, until_substring="world"
            )
            return scrollback_after, pid_buffer, world_buffer

    scrollback_after, pid_buffer, world_buffer = asyncio.run(_phase_two())

    assert "hello" in scrollback_after, (
        f"step 11: prior 'hello' missing from scrollback after restart; "
        f"scrollback_after={scrollback_after!r}"
    )
    assert pid_before in pid_buffer, (
        f"step 12: shell PID changed across orchestrator restart — "
        f"tmux durability broken. pid_before={pid_before!r} "
        f"post_restart_buffer={pid_buffer!r}"
    )
    assert "world" in world_buffer, (
        f"step 13: did not see 'world' echoed on the post-restart shell; "
        f"saw={world_buffer!r}"
    )

    # ----- step 14: tear down session + log redaction sweep --------------
    delete_status = _delete_session(backend_url, cookies, session_id)
    assert delete_status == 200, (
        f"step 14: DELETE /api/v1/sessions/{session_id} returned {delete_status}"
    )

    log_blob = _capture_compose_logs("orchestrator", "backend")
    log_path = "/tmp/m002_s01.log"
    try:
        with open(log_path, "w") as fp:
            fp.write(log_blob)
    except OSError:
        # Couldn't write to /tmp — non-fatal for the assertion logic.
        pass

    assert email not in log_blob, (
        f"step 14: seeded email leaked to compose logs (UUID-only invariant "
        f"violated). first occurrence at index {log_blob.find(email)}"
    )
    assert full_name not in log_blob, (
        f"step 14: seeded full_name leaked to compose logs (UUID-only "
        f"invariant violated). first occurrence at index "
        f"{log_blob.find(full_name)}"
    )

    # Smoke check that the observability taxonomy keys actually fired
    # somewhere in this run — a soft assertion using compose logs.
    for key in ("image_pull_ok", "session_created", "session_attached"):
        assert key in log_blob, (
            f"step 14: required INFO key {key!r} not seen in orchestrator/"
            f"backend logs (observability taxonomy regression)"
        )

    elapsed = time.time() - suite_started
    # The slice plan caps suite wall-clock at ≤60s, but the orchestrator
    # restart + healthcheck poll alone can eat 20s+ on cold compose. We
    # don't fail the test on this — just surface it as a warning-ish print
    # via pytest's report. Hard cap is 120s (defensive).
    assert elapsed < 120.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 60s budget; "
        f"investigate before relying on this in CI"
    )
