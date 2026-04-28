"""M004 / S05 / T03 — GitHub webhook receiver e2e against the live compose stack.

Slice S05's demo-truth statement: an external POST to
``/api/v1/github/webhooks`` with a valid ``X-Hub-Signature-256`` lands in
``github_webhook_events`` and fires the no-op ``dispatch_github_event``
hook (``webhook_dispatched`` log line). A bad signature persists to
``webhook_rejections`` (no body) and 401s. A duplicate ``delivery_id``
returns 200 idempotently without a second event row. A corrupted
``github_app_webhook_secret`` ciphertext surfaces as 503 via the global
``SystemSettingDecryptError`` handler — naming the row key, never leaking
the plaintext. A redaction sweep across the sibling backend's logs must
show zero matches for the captured webhook secret plaintext.

Flow against the live compose stack (sibling backend container — no
TestClient, no orchestrator swap; mirrors test_m004_s01_sensitive_settings_e2e.py):

  1. Skip-guard: probe ``backend:latest`` for the s06e alembic revision
     file. Skip with the canonical ``docker compose build backend`` hint
     if the baked image predates the migration (MEM147 / MEM162 / MEM186).
  2. Autouse cleanup: DELETE every row from ``github_webhook_events`` and
     ``webhook_rejections`` and every ``github_app_webhook_secret`` row
     before AND after the test. The compose ``app-db-data`` volume
     persists across runs (MEM161) so leftover rows from a previous pass
     would taint the assertions.
  3. Log in as the seeded FIRST_SUPERUSER (``admin@example.com``).
  4. POST ``github_app_webhook_secret/generate`` — capture the plaintext
     from the one-time-display response. Subsequent steps sign payloads
     with this value.
  5. Valid POST: synthetic push payload, ``X-Hub-Signature-256`` computed
     correctly. 200 → row in ``github_webhook_events`` → backend logs
     contain ``webhook_received`` + ``webhook_verified`` +
     ``webhook_dispatched`` for the same delivery_id.
  6. Idempotent POST: same payload, same signature, same delivery_id. 200
     → still exactly one event row.
  7. Invalid signature: same payload, one byte flipped in the hex digest.
     401 ``invalid_signature`` → row in ``webhook_rejections`` with
     ``signature_valid=false, signature_present=true`` → no new event row
     → WARNING ``webhook_signature_invalid`` log.
  8. Absent signature: no ``X-Hub-Signature-256`` header at all. 401
     ``invalid_signature`` → ``webhook_rejections`` row with
     ``signature_present=false``.
  9. Decrypt-failure 503-via-HTTP: corrupt the stored ciphertext via
     psql ``UPDATE``, then POST a payload with a HMAC-valid-by-old-secret
     header. The receiver loads the row, calls ``decrypt_setting`` on the
     corrupted bytes, the global handler in ``app/main.py`` translates
     the resulting ``SystemSettingDecryptError`` to 503 + ERROR log naming
     ``key=github_app_webhook_secret``. This is the first true
     503-via-HTTP test of the global handler — S01 T04 only proved the
     log shape via docker-exec.
 10. Redaction sweep: assert the captured webhook-secret plaintext does
     NOT appear anywhere in the backend container logs.

Wall-clock budget ≤ 30 s — no container provisioning beyond the existing
sibling backend booted by the ``backend_url`` fixture.

How to run::

    docker compose build backend
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m004_s05_webhook_receiver_e2e.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

NETWORK = "perpetuity_default"
BACKEND_IMAGE = "backend:latest"
S06E_REVISION = "s06e_github_webhook_events"

pytestmark = [pytest.mark.e2e]


# ----- helpers -----------------------------------------------------------


def _docker(
    *args: str, check: bool = True, capture: bool = True, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _login_only(
    base_url: str, *, email: str, password: str
) -> httpx.Cookies:
    """Log in an existing user. Returns a fresh cookie jar (MEM029)."""
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"admin login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _psql_exec(sql: str) -> subprocess.CompletedProcess[str]:
    """Fire-and-check psql against the compose db container."""
    return _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-c", sql,
        check=False,
    )


def _backend_container_name() -> str:
    """Discover the sibling backend container spawned by `backend_url`."""
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert names, f"no sibling backend container found; got {names!r}"
    return names[0]


def _backend_logs(container_name: str) -> str:
    r = _docker("logs", container_name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


def _backend_image_has_s06e() -> bool:
    """Probe `backend:latest` for the s06e alembic revision file. Per
    MEM147 the image bakes /app/backend/app/alembic/versions/, so a stale
    image will fail to upgrade and the e2e will be misleading."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S06E_REVISION}.py" in (r.stdout or "")


def _wipe_webhook_state() -> None:
    """Wipe webhook rows and the webhook secret system_settings row.

    The compose ``app-db-data`` volume persists across runs (MEM161); a
    leftover ``github_webhook_events`` row from a previous pass would
    bias the idempotency assertion that asserts exactly one row after a
    repeat post. The webhook-secret row is wiped so the test starts from
    a known-empty state and the generate-secret step is deterministic.
    """
    _psql_exec("DELETE FROM github_webhook_events")
    _psql_exec("DELETE FROM webhook_rejections")
    _psql_exec(
        "DELETE FROM system_settings WHERE key='github_app_webhook_secret'"
    )


def _sign(secret: str, body: bytes) -> str:
    """Compute the GitHub-compatible ``sha256=<hex>`` signature header."""
    digest = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def _flip_one_hex_char(sig: str) -> str:
    """Flip a single hex character in a ``sha256=<hex>`` signature.

    Mutating in the middle (rather than the prefix) keeps the header
    structurally valid (``sha256=`` prefix preserved, length unchanged) so
    the receiver progresses through the prefix check and into
    ``compare_digest`` — exactly the path we want to exercise.
    """
    assert sig.startswith("sha256=")
    hex_part = sig[len("sha256=") :]
    midpoint = len(hex_part) // 2
    original = hex_part[midpoint]
    # Pick a different hex digit deterministically.
    swapped = "0" if original != "0" else "1"
    return "sha256=" + hex_part[:midpoint] + swapped + hex_part[midpoint + 1 :]


# ----- autouse skip-guard + cleanup --------------------------------------


@pytest.fixture(autouse=True)
def _require_s06e_baked() -> None:
    """Skip if the backend image lacks s06e — the test would fail in a
    confusing way at alembic upgrade. The skip message points the
    operator to the exact `docker compose build backend` command."""
    if not _backend_image_has_s06e():
        pytest.skip(
            "backend:latest is missing the "
            f"{S06E_REVISION!r} alembic revision — run "
            "`docker compose build backend` so the image bakes the "
            "current /app/backend/app/alembic/versions/ tree."
        )


@pytest.fixture(autouse=True)
def _wipe_webhook_state_before_after() -> Iterator[None]:
    """Clear webhook rows + webhook-secret system_settings row before AND
    after the test so the test starts from the documented empty-table
    state regardless of what other tests left behind. Compose's
    ``app-db-data`` named volume persists across test runs (MEM161)."""
    _wipe_webhook_state()
    yield
    _wipe_webhook_state()


# ----- the test ----------------------------------------------------------


def test_full_webhook_contract_e2e(  # noqa: PLR0915
    backend_url: str,
) -> None:
    """Slice S05 demo: HMAC verify, idempotent persist, no-op dispatch,
    rejection persistence, decrypt-failure 503-via-HTTP, redaction sweep."""
    suite_started = time.time()

    backend_container = _backend_container_name()

    # ----- Smoke: zero webhook rows at start ---------------------------
    initial_events = _psql_one(
        "SELECT count(*) FROM github_webhook_events"
    )
    assert initial_events == "0", (
        "expected zero github_webhook_events rows at start; got "
        f"{initial_events!r}"
    )
    initial_rejections = _psql_one(
        "SELECT count(*) FROM webhook_rejections"
    )
    assert initial_rejections == "0", (
        "expected zero webhook_rejections rows at start; got "
        f"{initial_rejections!r}"
    )

    # ----- Step 1: log in as the seeded system_admin -------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )

    # ----- Step 2: generate the webhook secret (one-shot plaintext) ----
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_gen = c.post(
            "/api/v1/admin/settings/github_app_webhook_secret/generate"
        )
    assert r_gen.status_code == 200, (
        f"POST generate webhook_secret: {r_gen.status_code} {r_gen.text}"
    )
    gen_body = r_gen.json()
    assert gen_body["key"] == "github_app_webhook_secret"
    assert gen_body["has_value"] is True
    assert gen_body["generated"] is True
    webhook_secret = gen_body["value"]
    assert isinstance(webhook_secret, str) and len(webhook_secret) >= 32, (
        f"generated webhook secret too short: len={len(webhook_secret)}; "
        f"body={gen_body!r}"
    )

    # ----- Step 3: build a synthetic push payload ----------------------
    delivery_id = f"e2e-{uuid.uuid4().hex}"
    event_type = "push"
    payload_dict = {
        "action": "push",
        "ref": "refs/heads/main",
        "repository": {
            "id": 987654321,
            "full_name": "perpetuity-test/sample-repo",
        },
        "installation": {"id": 11111},
        "sender": {"login": "octocat"},
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    valid_sig = _sign(webhook_secret, raw_body)

    # ----- Step 4: valid POST → 200 + event row + 3 contract logs ------
    # NOTE: ``X-GitHub-Hook-Installation-Target-Id`` is intentionally omitted
    # — the route persists that header value into
    # ``github_webhook_events.installation_id`` which is a FK to
    # ``github_app_installations(installation_id)``. The S05 e2e seeds no
    # install row, so passing a synthetic id produces a FK ForeignKeyViolation
    # and a 500. Real GitHub deliveries hit the same path when a webhook
    # arrives before the install-bookkeeping row lands; treating that as a
    # 500 is a route hardening item for M005 (the dispatch-real slice owns
    # install discovery). For S05 we leave the column NULL — exercising the
    # ON-DELETE-SET-NULL FK posture T01 chose precisely for this case.
    headers_valid = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": valid_sig,
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": delivery_id,
    }
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        r_post1 = c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers=headers_valid,
        )
    assert r_post1.status_code == 200, (
        f"valid webhook POST: {r_post1.status_code} {r_post1.text}"
    )
    body1 = r_post1.json()
    assert body1.get("status") == "ok"
    assert body1.get("duplicate") is False, (
        f"first valid post should not be flagged duplicate; got {body1!r}"
    )

    # DB inspection: exactly one event row with the expected fields.
    event_row = _psql_one(
        "SELECT delivery_id || '|' || event_type || '|' || dispatch_status "
        "FROM github_webhook_events ORDER BY received_at DESC LIMIT 1"
    )
    assert event_row, "expected one github_webhook_events row after valid POST"
    got_did, got_etype, got_status = event_row.split("|")
    assert got_did == delivery_id, (
        f"event row delivery_id mismatch: {got_did!r} vs {delivery_id!r}"
    )
    assert got_etype == event_type
    assert got_status == "noop"

    # docker logs has a small flush lag — same idiom as the M004/S01 e2e.
    time.sleep(1.0)
    logs_after_post1 = _backend_logs(backend_container)
    expected_received = (
        f"webhook_received delivery_id={delivery_id} "
        f"event_type={event_type} source_ip="
    )
    expected_verified = (
        f"webhook_verified delivery_id={delivery_id} "
        f"event_type={event_type}"
    )
    expected_dispatched = (
        f"webhook_dispatched delivery_id={delivery_id} "
        f"event_type={event_type} dispatch_status=noop"
    )
    for line in (expected_received, expected_verified, expected_dispatched):
        assert line in logs_after_post1, (
            f"missing log line {line!r}; tail:\n{logs_after_post1[-2000:]}"
        )

    # ----- Step 5: idempotent re-POST → 200 + still one row -----------
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        r_post2 = c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers=headers_valid,
        )
    assert r_post2.status_code == 200, (
        f"idempotent webhook POST: {r_post2.status_code} {r_post2.text}"
    )
    body2 = r_post2.json()
    assert body2.get("duplicate") is True, (
        f"second post with same delivery_id must be flagged duplicate; "
        f"got {body2!r}"
    )

    event_count_after_dup = _psql_one(
        "SELECT count(*) FROM github_webhook_events "
        f"WHERE delivery_id='{delivery_id}'"
    )
    assert event_count_after_dup == "1", (
        "duplicate delivery_id must NOT produce a second row; "
        f"got count={event_count_after_dup!r}"
    )

    time.sleep(1.0)
    logs_after_post2 = _backend_logs(backend_container)
    expected_dup = f"webhook_duplicate_delivery delivery_id={delivery_id}"
    assert expected_dup in logs_after_post2, (
        f"missing log line {expected_dup!r}; "
        f"tail:\n{logs_after_post2[-2000:]}"
    )

    # ----- Step 6: invalid signature → 401 + rejection row ------------
    bad_delivery_id = f"e2e-bad-{uuid.uuid4().hex}"
    bad_sig = _flip_one_hex_char(valid_sig)
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        r_bad = c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": bad_sig,
                "X-GitHub-Event": event_type,
                "X-GitHub-Delivery": bad_delivery_id,
            },
        )
    assert r_bad.status_code == 401, (
        f"bad-signature POST: {r_bad.status_code} {r_bad.text}"
    )
    assert r_bad.json() == {"detail": "invalid_signature"}, (
        f"unexpected bad-sig body: {r_bad.json()!r}"
    )

    # webhook_rejections gained a row tied to bad_delivery_id.
    rej_row = _psql_one(
        "SELECT signature_present || '|' || signature_valid "
        f"FROM webhook_rejections WHERE delivery_id='{bad_delivery_id}'"
    )
    assert rej_row, (
        "expected webhook_rejections row for bad-signature POST keyed on "
        f"delivery_id={bad_delivery_id!r}"
    )
    sp_str, sv_str = rej_row.split("|")
    assert sp_str == "t" or sp_str == "true", (
        f"signature_present must be true (header was supplied); got {sp_str!r}"
    )
    assert sv_str == "f" or sv_str == "false", (
        f"signature_valid must be false on a bad signature; got {sv_str!r}"
    )

    # No new event row for the bad delivery id.
    bad_event_count = _psql_one(
        "SELECT count(*) FROM github_webhook_events "
        f"WHERE delivery_id='{bad_delivery_id}'"
    )
    assert bad_event_count == "0", (
        "bad-signature POST must NOT persist a github_webhook_events row; "
        f"got count={bad_event_count!r}"
    )

    time.sleep(1.0)
    logs_after_bad = _backend_logs(backend_container)
    expected_warn = (
        f"webhook_signature_invalid delivery_id={bad_delivery_id} "
        "source_ip="
    )
    assert expected_warn in logs_after_bad, (
        f"missing WARNING line {expected_warn!r}; "
        f"tail:\n{logs_after_bad[-2000:]}"
    )

    # ----- Step 7: absent X-Hub-Signature-256 header → 401 ------------
    nosig_delivery_id = f"e2e-nosig-{uuid.uuid4().hex}"
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        r_nosig = c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": event_type,
                "X-GitHub-Delivery": nosig_delivery_id,
            },
        )
    assert r_nosig.status_code == 401, (
        f"no-signature POST: {r_nosig.status_code} {r_nosig.text}"
    )
    assert r_nosig.json() == {"detail": "invalid_signature"}

    nosig_rej = _psql_one(
        "SELECT signature_present || '|' || signature_valid "
        f"FROM webhook_rejections WHERE delivery_id='{nosig_delivery_id}'"
    )
    assert nosig_rej, (
        "expected webhook_rejections row for absent-signature POST"
    )
    sp_str, sv_str = nosig_rej.split("|")
    assert sp_str == "f" or sp_str == "false", (
        f"signature_present must be false when header absent; got {sp_str!r}"
    )
    assert sv_str == "f" or sv_str == "false"

    # ----- Step 8: decrypt-failure 503-via-HTTP -----------------------
    # Corrupt the stored ciphertext directly via psql, then POST a payload
    # whose HMAC was computed against the (now-uncoverable) old plaintext.
    # The receiver loads the row, calls decrypt_setting on the corrupted
    # bytes, the encryption module raises SystemSettingDecryptError, and
    # the global handler in app/main.py translates it to 503 +
    # `system_settings_decrypt_failed key=github_app_webhook_secret` log.
    corrupt_sql = (
        "UPDATE system_settings "
        "SET value_encrypted = E'\\\\xdeadbeef' "
        "WHERE key='github_app_webhook_secret'"
    )
    upd = _psql_exec(corrupt_sql)
    assert upd.returncode == 0, (
        f"psql UPDATE failed; rc={upd.returncode} stderr={upd.stderr!r}"
    )
    corrupted_len = _psql_one(
        "SELECT length(value_encrypted) "
        "FROM system_settings WHERE key='github_app_webhook_secret'"
    )
    assert corrupted_len == "4", (
        f"corrupted value_encrypted should be 4 bytes; got {corrupted_len!r}"
    )

    decrypt_delivery_id = f"e2e-decrypt-{uuid.uuid4().hex}"
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        r_503 = c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": valid_sig,
                "X-GitHub-Event": event_type,
                "X-GitHub-Delivery": decrypt_delivery_id,
            },
        )
    assert r_503.status_code == 503, (
        "corrupted-ciphertext POST must surface as 503 via the global "
        f"handler; got {r_503.status_code} {r_503.text}"
    )
    body_503 = r_503.json()
    assert body_503 == {
        "detail": "system_settings_decrypt_failed",
        "key": "github_app_webhook_secret",
    }, f"unexpected 503 body: {body_503!r}"

    time.sleep(1.0)
    logs_after_503 = _backend_logs(backend_container)
    expected_decrypt_log = (
        "system_settings_decrypt_failed key=github_app_webhook_secret"
    )
    assert expected_decrypt_log in logs_after_503, (
        f"missing ERROR log {expected_decrypt_log!r} after 503 path; "
        f"tail:\n{logs_after_503[-2000:]}"
    )

    # No event row for the decrypt-failure delivery id either.
    decrypt_event_count = _psql_one(
        "SELECT count(*) FROM github_webhook_events "
        f"WHERE delivery_id='{decrypt_delivery_id}'"
    )
    assert decrypt_event_count == "0", (
        "decrypt-failure POST must NOT persist a github_webhook_events row; "
        f"got count={decrypt_event_count!r}"
    )

    # ----- Step 9: redaction sweep ------------------------------------
    # The captured plaintext webhook secret MUST NOT appear anywhere in
    # the backend logs. The secret is freshly generated per run so a hit
    # is unambiguously a leak, not coincidence.
    final_logs = _backend_logs(backend_container)
    assert webhook_secret not in final_logs, (
        "redaction sweep: webhook secret plaintext leaked into backend logs"
    )

    # Smoke: the slice's full observability taxonomy fired.
    for marker in (
        "webhook_received",
        "webhook_verified",
        "webhook_dispatched",
        "webhook_signature_invalid",
        "system_settings_decrypt_failed",
    ):
        assert marker in final_logs, (
            f"observability taxonomy regression: {marker!r} not seen in "
            "backend logs"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤30 s; tolerate up to 90 s defensively because
    # docker exec + log flush adds a few seconds on slower hosts.
    assert elapsed < 90.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 30s slice budget"
    )
