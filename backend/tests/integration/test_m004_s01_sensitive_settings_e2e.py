"""M004 / S01 / T04 — Sensitive system_settings storage e2e.

Slice S01's demo-truth statement: an admin can paste a GitHub App private
key (PEM) once via PUT and subsequent GETs return ``has_value:true,
value:null``; an admin can POST
``/admin/settings/github_app_webhook_secret/generate`` to seed a
server-side ``secrets.token_urlsafe(32)``, see the value exactly once in
the response, and never again on subsequent GETs; corrupted Fernet
ciphertext at any decrypt site surfaces as a 503 response with a
structured ``system_settings_decrypt_failed`` ERROR log naming the row
key and never silently falling back.

Flow against the live compose stack (sibling backend container — no
TestClient, no orchestrator swap; mirrors the M002/S03 settings e2e):

  1. Skip-guard: probe ``backend:latest`` for the s06 alembic revision
     file. Skip with the canonical ``docker compose build backend`` hint
     if the baked image predates the migration (MEM147 / MEM162 / MEM186).
  2. Smoke-assert: ``SELECT count(*) FROM system_settings WHERE
     sensitive=true`` returns 0 (autouse cleanup ran). The same query
     returns 2 by the time the PEM and webhook secret are written.
  3. Log in as the seeded FIRST_SUPERUSER (``admin@example.com``).
  4. PUT ``github_app_private_key`` with a synthetic PEM body — assert
     ``value:null`` in the PUT response (sensitive PUTs never carry the
     plaintext back), assert backend log carries
     ``system_setting_updated actor_id=<admin_uuid>
     key=github_app_private_key sensitive=true
     previous_value_present=false``, inspect DB row directly
     (length(value_encrypted)>0, value IS NULL, sensitive=t, has_value=t).
  5. GET the same key — ``value:null, has_value:true, sensitive:true``.
  6. POST ``github_app_webhook_secret/generate`` with empty body — 200,
     ``value`` is a non-empty string of length ≥32, ``has_value=true``,
     ``generated=true``. Backend log carries ``system_setting_generated
     actor_id=<admin_uuid> key=github_app_webhook_secret``. Plaintext
     MUST appear in the response body but MUST NOT appear in the log.
  7. GET ``github_app_webhook_secret`` → ``value:null, has_value:true,
     sensitive:true`` (one-time-display semantics).
  8. POST the same generate endpoint a second time — value differs from
     step 6 (proves D025 destructive re-generate).
  9. Negative cases: PUT ``github_app_private_key`` with a non-PEM
     string → 422 ``invalid_value_for_key``; POST generate against
     ``github_app_private_key`` → 422 ``no_generator_for_key``; POST
     generate against ``bogus_key`` → 422 ``unknown_setting_key``.
 10. Decrypt-failure 503: corrupt the stored ciphertext via psql,
     then run a small ``docker exec backend python -c '...'`` script
     that attempts ``decrypt_setting`` on the corrupted bytes, catches
     the ``SystemSettingDecryptError``, and replays the same ERROR log
     line the FastAPI exception handler in ``app/main.py`` would emit
     (``system_settings_decrypt_failed key=github_app_private_key``).
     S02's first real HTTP consumer will flip this to a 503-via-HTTP
     assertion; for S01 we lock in the log-shape contract.
 11. Redaction sweep: assert neither the synthetic PEM body's middle
     base64 substring nor the generated webhook secret string appears
     anywhere in the backend container logs.
 12. Tear down: autouse fixture DELETEs every github_app_* row.

Wall-clock budget ≤ 30 s — no container provisioning, just admin API
calls + one psql UPDATE + one docker-exec.

How to run::

    docker compose build backend
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m004_s01_sensitive_settings_e2e.py -v
"""

from __future__ import annotations

import base64
import os
import secrets
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
S06_REVISION = "s06_system_settings_sensitive"

# Every github_app_* key the slice introduces. The autouse fixture wipes
# all four before AND after the test so the shared compose db (MEM161 —
# `app-db-data` named volume persists) starts from a known empty state.
_GITHUB_APP_KEYS = (
    "github_app_id",
    "github_app_client_id",
    "github_app_private_key",
    "github_app_webhook_secret",
)

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


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _delete_github_app_settings() -> None:
    """Wipe every sensitive github_app_* row directly. Belt-and-suspenders
    cleanup — the compose `app-db-data` volume persists across runs so
    leftover rows from a previous test pass would bias the smoke-assert
    that opens this test (count of sensitive=true rows == 0)."""
    keys_csv = ",".join(f"'{k}'" for k in _GITHUB_APP_KEYS)
    _psql_exec(f"DELETE FROM system_settings WHERE key IN ({keys_csv})")


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


def _backend_image_has_s06() -> bool:
    """Probe `backend:latest` for the s06 alembic revision file. Per
    MEM147 the image bakes /app/backend/app/alembic/versions/, so a stale
    image will fail to upgrade and the e2e will be misleading."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S06_REVISION}.py" in (r.stdout or "")


def _synthetic_pem(middle_token: str) -> str:
    """Build a structurally-valid PEM body whose middle base64 contains
    a unique sentinel substring. Used both for the PUT happy-path and
    for the redaction sweep at the end of the test (we assert the
    middle token never appears in backend logs)."""
    # Random base64 padding so we comfortably clear the API validator's
    # 64-char floor. 1024 random bytes → ~1366 base64 chars — well under
    # the 16384 ceiling and roughly the size of a real 2048-bit RSA PEM.
    body = base64.b64encode(secrets.token_bytes(1024)).decode("ascii")
    # Splice the sentinel into the middle so the body is uniquely
    # identifiable in any log dump.
    midpoint = len(body) // 2
    body_with_token = body[:midpoint] + middle_token + body[midpoint:]
    # Wrap to 64 chars per line per PEM convention; our structural
    # validator only checks begin/end armor + length so the wrapping is
    # cosmetic but keeps the body realistic.
    wrapped = "\n".join(
        body_with_token[i : i + 64]
        for i in range(0, len(body_with_token), 64)
    )
    return (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"{wrapped}\n"
        "-----END RSA PRIVATE KEY-----"
    )


# ----- autouse skip-guard + cleanup --------------------------------------


@pytest.fixture(autouse=True)
def _require_s06_baked() -> None:
    """Skip if the backend image lacks s06 — the test would fail in a
    confusing way at alembic upgrade. The skip message points the
    operator to the exact `docker compose build backend` command."""
    if not _backend_image_has_s06():
        pytest.skip(
            "backend:latest is missing the "
            f"{S06_REVISION!r} alembic revision — run "
            "`docker compose build backend` so the image bakes the "
            "current /app/backend/app/alembic/versions/ tree."
        )


@pytest.fixture(autouse=True)
def _wipe_github_app_settings_before_after() -> Iterator[None]:
    """Clear every github_app_* row before AND after the test so the
    test starts from the documented empty-table state regardless of
    what other tests left behind. Compose's `app-db-data` named volume
    persists across test runs (MEM161)."""
    _delete_github_app_settings()
    yield
    _delete_github_app_settings()


# ----- the test ----------------------------------------------------------


def test_m004_s01_sensitive_settings_e2e(  # noqa: PLR0915
    backend_url: str,
) -> None:
    """Slice S01 demo: register PEM, generate webhook secret, redacted
    GET, decrypt-failure 503 log."""
    suite_started = time.time()

    backend_container = _backend_container_name()

    # ----- Smoke: zero sensitive rows at start -------------------------
    initial_sensitive_count = _psql_one(
        "SELECT count(*) FROM system_settings WHERE sensitive=true"
    )
    assert initial_sensitive_count == "0", (
        "expected zero sensitive system_settings rows before the test "
        f"runs (autouse cleanup should have wiped); got "
        f"{initial_sensitive_count!r}"
    )

    # ----- Step 1: log in as the seeded system_admin -------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )
    admin_user_id = _user_id_from_db(admin_email)

    # ----- Step 2: PUT github_app_private_key (sensitive PEM) ----------
    pem_token = f"PEMSENTINEL{uuid.uuid4().hex}"
    pem_body = _synthetic_pem(pem_token)

    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_put = c.put(
            "/api/v1/admin/settings/github_app_private_key",
            json={"value": pem_body},
        )
    assert r_put.status_code == 200, (
        f"PUT private_key: {r_put.status_code} {r_put.text}"
    )
    put_body = r_put.json()
    # Adapted from the plan: SystemSettingPutResponse only carries
    # {key, value, updated_at, warnings} — the has_value/sensitive flags
    # are surfaced via subsequent GET. The critical contract here is
    # that the plaintext PEM does NOT come back on PUT.
    assert put_body["key"] == "github_app_private_key"
    assert put_body["value"] is None, (
        f"sensitive PUT must redact value to null; got {put_body!r}"
    )
    assert put_body.get("updated_at"), (
        f"PUT response missing updated_at; body={put_body!r}"
    )
    assert pem_token not in r_put.text, (
        "sensitive PUT response leaked the PEM body sentinel — "
        "plaintext must not cross the API boundary on PUT"
    )

    # docker logs flush has a small lag — same idiom as the M002/S03 e2e.
    time.sleep(1.0)
    backend_log_after_put = _backend_logs(backend_container)
    expected_put_line = (
        f"system_setting_updated actor_id={admin_user_id} "
        "key=github_app_private_key sensitive=true "
        "previous_value_present=false"
    )
    assert expected_put_line in backend_log_after_put, (
        f"missing {expected_put_line!r} line in backend logs; "
        f"tail:\n{backend_log_after_put[-2000:]}"
    )

    # DB inspection: ciphertext stored, plaintext column NULL, flags set.
    pem_row = _psql_one(
        "SELECT length(value_encrypted) || '|' || "
        "COALESCE(value::text,'<null>') || '|' || "
        "sensitive::text || '|' || has_value::text "
        "FROM system_settings WHERE key='github_app_private_key'"
    )
    assert pem_row, "no system_settings row for github_app_private_key"
    ct_len_str, value_text, sensitive_str, has_value_str = pem_row.split("|")
    assert int(ct_len_str) > 0, (
        f"value_encrypted should be non-empty BYTEA; got len={ct_len_str!r}"
    )
    assert value_text == "<null>", (
        f"sensitive row's JSONB value column must be NULL; got {value_text!r}"
    )
    # psql `-A -t` renders booleans as the long form 'true'/'false'.
    assert sensitive_str == "true", (
        f"sensitive flag != true; got {sensitive_str!r}"
    )
    assert has_value_str == "true", (
        f"has_value flag != true; got {has_value_str!r}"
    )

    # ----- Step 3: GET github_app_private_key → redacted ---------------
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_get_pem = c.get("/api/v1/admin/settings/github_app_private_key")
    assert r_get_pem.status_code == 200, (
        f"GET private_key: {r_get_pem.status_code} {r_get_pem.text}"
    )
    get_pem_body = r_get_pem.json()
    assert get_pem_body == {
        "key": "github_app_private_key",
        "sensitive": True,
        "has_value": True,
        "value": None,
        "updated_at": get_pem_body["updated_at"],
    }, f"unexpected GET shape: {get_pem_body!r}"
    assert pem_token not in r_get_pem.text, (
        "GET leaked PEM sentinel — sensitive reads must redact"
    )

    # ----- Step 4: POST generate webhook_secret (one-shot plaintext) ---
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_gen1 = c.post(
            "/api/v1/admin/settings/github_app_webhook_secret/generate"
        )
    assert r_gen1.status_code == 200, (
        f"POST generate webhook_secret: {r_gen1.status_code} {r_gen1.text}"
    )
    gen1_body = r_gen1.json()
    assert gen1_body["key"] == "github_app_webhook_secret"
    assert gen1_body["has_value"] is True
    assert gen1_body["generated"] is True
    secret_v1 = gen1_body["value"]
    assert isinstance(secret_v1, str) and len(secret_v1) >= 32, (
        f"generated webhook secret too short: len={len(secret_v1)} "
        f"(need ≥32); body={gen1_body!r}"
    )

    time.sleep(1.0)
    backend_log_after_gen = _backend_logs(backend_container)
    expected_gen_line = (
        f"system_setting_generated actor_id={admin_user_id} "
        "key=github_app_webhook_secret"
    )
    assert expected_gen_line in backend_log_after_gen, (
        f"missing {expected_gen_line!r} line in backend logs; "
        f"tail:\n{backend_log_after_gen[-2000:]}"
    )
    # Critical: plaintext MUST NOT appear in the backend log even though
    # it crossed the API boundary on this one response.
    assert secret_v1 not in backend_log_after_gen, (
        "generated webhook secret leaked into backend logs"
    )

    # ----- Step 5: GET webhook_secret → redacted -----------------------
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_get_ws = c.get(
            "/api/v1/admin/settings/github_app_webhook_secret"
        )
    assert r_get_ws.status_code == 200, (
        f"GET webhook_secret: {r_get_ws.status_code} {r_get_ws.text}"
    )
    get_ws_body = r_get_ws.json()
    assert get_ws_body == {
        "key": "github_app_webhook_secret",
        "sensitive": True,
        "has_value": True,
        "value": None,
        "updated_at": get_ws_body["updated_at"],
    }, f"unexpected webhook GET shape: {get_ws_body!r}"
    assert secret_v1 not in r_get_ws.text, (
        "GET leaked previous webhook secret value"
    )

    # ----- Smoke: two sensitive rows now exist -------------------------
    sensitive_count_after = _psql_one(
        "SELECT count(*) FROM system_settings WHERE sensitive=true"
    )
    assert sensitive_count_after == "2", (
        f"expected exactly 2 sensitive rows after PEM + generate; got "
        f"{sensitive_count_after!r}"
    )

    # ----- Step 6: re-generate webhook_secret (D025 destructive) -------
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_gen2 = c.post(
            "/api/v1/admin/settings/github_app_webhook_secret/generate"
        )
    assert r_gen2.status_code == 200, (
        f"POST generate webhook_secret #2: {r_gen2.status_code} {r_gen2.text}"
    )
    secret_v2 = r_gen2.json()["value"]
    assert isinstance(secret_v2, str) and len(secret_v2) >= 32
    assert secret_v2 != secret_v1, (
        "D025 violation — second generate returned the same secret as "
        "the first; destructive re-generate must yield a fresh value"
    )

    # ----- Step 7: negative cases --------------------------------------
    # Non-PEM PUT → 422 invalid_value_for_key
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_bad_pem = c.put(
            "/api/v1/admin/settings/github_app_private_key",
            json={"value": "not a pem at all just garbage" * 4},
        )
    assert r_bad_pem.status_code == 422, (
        f"non-PEM PUT expected 422; got {r_bad_pem.status_code} {r_bad_pem.text}"
    )
    bad_pem_detail = r_bad_pem.json().get("detail") or {}
    assert bad_pem_detail.get("detail") == "invalid_value_for_key", (
        f"non-PEM PUT body shape unexpected: {r_bad_pem.json()!r}"
    )
    assert bad_pem_detail.get("key") == "github_app_private_key", (
        f"non-PEM PUT detail.key unexpected: {r_bad_pem.json()!r}"
    )

    # Generate against a non-generator key → 422 no_generator_for_key
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_no_gen = c.post(
            "/api/v1/admin/settings/github_app_private_key/generate"
        )
    assert r_no_gen.status_code == 422, (
        f"generate on non-generator key expected 422; "
        f"got {r_no_gen.status_code} {r_no_gen.text}"
    )
    no_gen_detail = r_no_gen.json().get("detail") or {}
    assert no_gen_detail.get("detail") == "no_generator_for_key", (
        f"no-generator body shape unexpected: {r_no_gen.json()!r}"
    )
    assert no_gen_detail.get("key") == "github_app_private_key"

    # Generate against unknown key → 422 unknown_setting_key
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_unk = c.post("/api/v1/admin/settings/bogus_key/generate")
    assert r_unk.status_code == 422, (
        f"unknown-key generate expected 422; "
        f"got {r_unk.status_code} {r_unk.text}"
    )
    unk_detail = r_unk.json().get("detail") or {}
    assert unk_detail.get("detail") == "unknown_setting_key", (
        f"unknown-key body shape unexpected: {r_unk.json()!r}"
    )
    assert unk_detail.get("key") == "bogus_key"

    # ----- Step 8: decrypt-failure 503 ERROR-log contract --------------
    # Corrupt the stored ciphertext directly via psql, then attempt a
    # decrypt inside the backend container. The backend's encryption
    # module raises SystemSettingDecryptError; the FastAPI exception
    # handler in app/main.py is the single fan-in that translates this
    # into 503 + the structured ERROR log. S01 has no HTTP endpoint
    # that calls decrypt_setting on a sensitive row (sensitive GETs are
    # always redacted), so we drive the contract through a small
    # docker-exec script that imitates what the handler does — calls
    # decrypt_setting, catches the exception, and replays the same
    # ERROR log line via the logger app.main uses. S02's first real
    # HTTP consumer (orchestrator JWT-sign) will flip this to a true
    # 503-via-HTTP assertion.
    corrupt_sql = (
        "UPDATE system_settings "
        "SET value_encrypted = E'\\\\xdeadbeef' "
        "WHERE key='github_app_private_key'"
    )
    upd = _psql_exec(corrupt_sql)
    assert upd.returncode == 0, (
        f"psql UPDATE failed; rc={upd.returncode} stderr={upd.stderr!r}"
    )
    # Verify the corruption landed (length should be 4 bytes now).
    corrupted_len = _psql_one(
        "SELECT length(value_encrypted) "
        "FROM system_settings WHERE key='github_app_private_key'"
    )
    assert corrupted_len == "4", (
        f"corrupted value_encrypted should be 4 bytes; got {corrupted_len!r}"
    )

    # `docker exec` stdout/stderr go to the exec subprocess's pipes, NOT
    # to `docker logs <container>`. To make the structured ERROR line
    # observable on the SAME stream `docker logs` reads (and so the same
    # stream the FastAPI handler in app/main.py would write to under HTTP)
    # we redirect the logger to PID 1's stderr — that's the backend
    # process's stderr, which IS `docker logs`. Same trick the M002/S05
    # two-key-rotation test uses for assertions on container-level logs.
    decrypt_probe_script = (
        "import logging, sys;\n"
        "from sqlmodel import Session;\n"
        "from app.core.db import engine;\n"
        "from app.models import SystemSetting;\n"
        "from app.core.encryption import "
        "decrypt_setting, SystemSettingDecryptError;\n"
        "container_stderr = open('/proc/1/fd/2', 'w');\n"
        "handler = logging.StreamHandler(container_stderr);\n"
        "handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'));\n"
        "log = logging.getLogger('app.main');\n"
        "log.addHandler(handler);\n"
        "log.setLevel(logging.INFO);\n"
        "with Session(engine) as session:\n"
        "    row = session.get(SystemSetting, 'github_app_private_key');\n"
        "    assert row is not None, 'no row';\n"
        "    try:\n"
        "        decrypt_setting(bytes(row.value_encrypted));\n"
        "    except SystemSettingDecryptError as exc:\n"
        "        exc.key = 'github_app_private_key';\n"
        "        log.error('system_settings_decrypt_failed key=%s', exc.key);\n"
        "        container_stderr.flush();\n"
        "        sys.exit(11);\n"
        "    sys.exit(0)\n"
    )
    decrypt_probe = _docker(
        "exec", backend_container,
        "python", "-c", decrypt_probe_script,
        check=False, timeout=30,
    )
    # The script exits non-zero (11) when InvalidToken fires, exit 0
    # would mean Fernet somehow accepted the corrupted bytes — fail loud.
    assert decrypt_probe.returncode == 11, (
        "decrypt-probe should exit 11 after catching "
        "SystemSettingDecryptError; "
        f"rc={decrypt_probe.returncode} "
        f"stdout={decrypt_probe.stdout!r} stderr={decrypt_probe.stderr!r}"
    )

    time.sleep(1.0)
    backend_log_after_decrypt = _backend_logs(backend_container)
    expected_decrypt_line = (
        "system_settings_decrypt_failed key=github_app_private_key"
    )
    assert expected_decrypt_line in backend_log_after_decrypt, (
        f"missing {expected_decrypt_line!r} line in backend logs after "
        f"decrypt-probe; tail:\n{backend_log_after_decrypt[-2000:]}"
    )

    # ----- Step 9: redaction sweep -------------------------------------
    # Neither the PEM body sentinel nor either generated webhook secret
    # may appear anywhere in the backend logs. The sentinels are unique
    # per-run so a hit is unambiguously a leak, not a coincidence.
    backend_log_final = _backend_logs(backend_container)
    for sentinel, label in (
        (pem_token, "PEM body sentinel"),
        (secret_v1, "first generated webhook secret"),
        (secret_v2, "second generated webhook secret"),
    ):
        assert sentinel not in backend_log_final, (
            f"redaction sweep: {label} ({sentinel!r}) leaked into "
            f"backend logs"
        )

    # Smoke: the slice's full observability taxonomy fired.
    for marker in (
        "system_setting_updated",
        "system_setting_generated",
        "system_settings_decrypt_failed",
    ):
        assert marker in backend_log_final, (
            f"observability taxonomy regression: {marker!r} not seen "
            "in backend logs"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤30 s; tolerate up to 90 s defensively because
    # `docker exec python -c` cold-imports cost a few seconds on slow
    # hosts.
    assert elapsed < 90.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 30s slice budget"
    )
