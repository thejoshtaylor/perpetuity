"""M005 / S01 / T05 — Team-scoped team_secrets storage e2e.

Slice S01's demo-truth statement: a team admin can paste a Claude API key
(`sk-ant-...`) and an OpenAI key (`sk-...`) once via PUT and subsequent
GETs return `has_value:true` with the value never echoed; replacing a key
bumps `updated_at`; DELETE clears presence; non-admin members get 403 on
PUT; bad-prefix values fail validation with 400 `invalid_value_shape`;
`get_team_secret` round-trips successfully through a local-only
test-decrypt endpoint; corrupted ciphertext at the same site surfaces as
a 503 `team_secret_decrypt_failed` ERROR log.

Eight test cases driven against the live compose stack (sibling backend
container — no TestClient, no orchestrator swap; mirrors M004/S01):

  (a) Team admin PUTs claude + openai keys; GET-list shows has_value:true
      for both, GET-single shows the same.
  (b) Replace PUT bumps updated_at strictly later than the first PUT.
  (c) DELETE clears claude key; subsequent GET-single returns 404
      `team_secret_not_set`; GET-list shows has_value:false for that key.
  (d) Non-admin team member PUT → 403 `team_admin_required`.
  (e) Bad-prefix value (e.g. `xai-...` for `claude_api_key`) → 400
      `invalid_value_shape` with hint=`bad_prefix`.
  (f) Round-trip via the local-only `_test_decrypt` endpoint exercises
      `get_team_secret` and returns the plaintext to a system_admin caller.
  (g) Corrupt the row's `value_encrypted` directly via psql; the same
      `_test_decrypt` call surfaces 503 `team_secret_decrypt_failed` and
      emits the locked ERROR log line.
  (h) Final redaction sweep over `docker logs <backend>`: zero `sk-ant-`
      or `sk-`-prefixed bearer-key matches. Source-file sweep
      (`scripts/redaction-sweep.sh`) is run separately by the slice
      verification command.

Skip-guard (MEM162 / MEM186 / MEM247): probes `backend:latest` for the
`s09_team_secrets` alembic revision; skips with the canonical
`docker compose build backend` hint when absent.

Wall-clock budget ≤ 30 s — admin API calls, one signup, one psql UPDATE,
one direct decrypt round-trip.

How to run::

    docker compose build backend
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m005_s01_team_secrets_e2e.py -v
"""

from __future__ import annotations

import os
import re
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
S09_REVISION = "s09_team_secrets"

# Synthetic key bodies — structurally valid (right prefix, ≥40 chars) but
# unique sentinels per-run so the redaction sweep at the end can prove
# THIS test's plaintext didn't leak (vs. coincidentally matching some
# pre-existing log line). Sentinels are appended to the prefix so the
# whole thing still satisfies the validator.
_RUN_TOKEN = uuid.uuid4().hex
CLAUDE_KEY = f"sk-ant-api03-{_RUN_TOKEN}-CLAUDESENTINEL-padpadpadpadpad"
OPENAI_KEY = f"sk-{_RUN_TOKEN}-OPENAISENTINEL-padpadpadpadpadpad"
CLAUDE_KEY_REPLACEMENT = (
    f"sk-ant-api03-{_RUN_TOKEN}-CLAUDESENTINEL2-padpadpadpadpa"
)
# Bad-prefix probe — has the shape of a Grok key, will fail the
# `sk-ant-` prefix check on PUT to claude_api_key.
BAD_PREFIX_VALUE = f"xai-{_RUN_TOKEN}-NOTACLAUDEKEY-padpadpadpadpadpadpad"

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
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _signup_login(
    base_url: str, *, email: str, password: str, full_name: str
) -> httpx.Cookies:
    """Sign up a fresh user, then log in. Returns the login cookie jar."""
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/signup",
            json={
                "email": email,
                "password": password,
                "full_name": full_name,
            },
        )
        assert r.status_code == 200, f"signup: {r.status_code} {r.text}"
        c.cookies.clear()
        r = c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


# Honor the same POSTGRES_DB override the conftest uses so direct SQL
# probes hit the same DB the ephemeral backend was launched against
# (MEM348 — the default `app` DB is occasionally contaminated and
# operators redirect to a clean `perpetuity_app`).
_PG_DB = os.environ.get("POSTGRES_DB", "app")


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", _PG_DB, "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _psql_exec(sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", _PG_DB, "-c", sql,
        check=False,
    )


def _backend_container_name() -> str:
    """Discover the sibling backend container spawned by the conftest."""
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


def _backend_image_has_s09() -> bool:
    """Probe `backend:latest` for the s09 alembic revision file. Per
    MEM147 the image bakes /app/backend/app/alembic/versions/, so a stale
    image will fail to upgrade and the e2e will be misleading."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S09_REVISION}.py" in (r.stdout or "")


def _list_team_id(base_url: str, cookies: httpx.Cookies) -> str:
    """Create a non-personal team via POST /teams and return its id.

    Personal teams are admin-of-self by default but the slice's bearer-key
    surface is meant for shared org keys, so we exercise it against a
    bona-fide non-personal team like a real operator would.
    """
    name = f"e2e-m005-s01-{_RUN_TOKEN[:8]}"
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post("/api/v1/teams/", json={"name": name})
    assert r.status_code == 200, f"create team: {r.status_code} {r.text}"
    return r.json()["id"]


def _add_member(team_id: str, user_id: str) -> None:
    """Splice a row into team_member directly so the test does not depend
    on the slice's invite-flow (which has its own e2e). Role defaults to
    'member' — the case (d) negative test specifically wants a non-admin.

    `team_member.id` is a non-default UUID PK; we generate one client-side
    so a fresh row can land without bumping into the unique
    `(user_id, team_id)` constraint via ON CONFLICT.
    """
    new_id = uuid.uuid4()
    out = _psql_exec(
        f"INSERT INTO team_member (id, user_id, team_id, role, created_at) "
        f"VALUES ('{new_id}', '{user_id}', '{team_id}', 'member', NOW()) "
        "ON CONFLICT (user_id, team_id) DO NOTHING"
    )
    assert out.returncode == 0, (
        f"INSERT team_member failed; rc={out.returncode} stderr={out.stderr!r}"
    )


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _delete_team_secrets_for(team_id: str) -> None:
    """Belt-and-suspenders cleanup — compose's named volume persists."""
    _psql_exec(f"DELETE FROM team_secrets WHERE team_id = '{team_id}'")


def _delete_team_and_members(team_id: str) -> None:
    # team_member rows would block the team delete via FK; cascade down.
    _psql_exec(f"DELETE FROM team_member WHERE team_id = '{team_id}'")
    _psql_exec(f"DELETE FROM team WHERE id = '{team_id}'")


def _delete_user_by_email(email: str) -> None:
    """Best-effort cleanup of test-created users + their personal team."""
    user_id = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    if not user_id:
        return
    # Personal team rows have FKs onto user; tear membership first then
    # the team itself, then the user.
    _psql_exec(f"DELETE FROM team_member WHERE user_id = '{user_id}'")
    _psql_exec(
        f"DELETE FROM team WHERE id IN (SELECT team_id FROM team_member "
        f"WHERE user_id = '{user_id}') OR (is_personal=TRUE AND id NOT IN "
        f"(SELECT team_id FROM team_member))"
    )
    _psql_exec(f"DELETE FROM \"user\" WHERE id = '{user_id}'")


# ----- autouse skip-guard (MEM162) ---------------------------------------


@pytest.fixture(autouse=True)
def _require_s09_baked() -> None:
    """Skip if the backend image lacks s09 — alembic upgrade would fail
    with a confusing 'Can't locate revision' message otherwise."""
    if not _backend_image_has_s09():
        pytest.skip(
            "backend:latest is missing the "
            f"{S09_REVISION!r} alembic revision — run "
            "`docker compose build backend` so the image bakes the "
            "current /app/backend/app/alembic/versions/ tree."
        )


# ----- the test ----------------------------------------------------------


def test_m005_s01_team_secrets_e2e(  # noqa: PLR0915
    backend_url: str,
) -> None:
    """Slice S01 demo: register Claude+OpenAI keys, replace+delete,
    role-gate, validator-gate, round-trip decrypt, tamper-detect 503,
    redaction sweep over container logs."""
    suite_started = time.time()

    backend_container = _backend_container_name()

    # ----- Step 1: log in as the seeded system_admin -------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )

    # Create a team and grab its id. The admin owns this team (POST
    # /teams seeds the creator as a TeamRole.admin row).
    team_id = _list_team_id(backend_url, admin_cookies)

    # Create a non-admin member in a separate user account for case (d).
    member_email = f"e2e-m005-member-{_RUN_TOKEN[:8]}@example.com"
    member_password = "changethis-member-x"
    member_cookies = _signup_login(
        backend_url,
        email=member_email,
        password=member_password,
        full_name="M005 Member",
    )
    member_user_id = _user_id_from_db(member_email)
    _add_member(team_id, member_user_id)

    try:
        # =====================================================================
        # Case (a) — admin pastes both keys; presence flips on subsequent GETs
        # =====================================================================
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_put_claude = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": CLAUDE_KEY},
            )
            r_put_openai = c.put(
                f"/api/v1/teams/{team_id}/secrets/openai_api_key",
                json={"value": OPENAI_KEY},
            )
        assert r_put_claude.status_code == 200, (
            f"PUT claude: {r_put_claude.status_code} {r_put_claude.text}"
        )
        assert r_put_openai.status_code == 200, (
            f"PUT openai: {r_put_openai.status_code} {r_put_openai.text}"
        )
        # Plaintext must NOT come back on PUT — the response is the same
        # status DTO GET-single returns.
        for label, body in (
            ("claude PUT", r_put_claude.json()),
            ("openai PUT", r_put_openai.json()),
        ):
            assert "value" not in body, (
                f"{label} response leaked a value field: {body!r}"
            )
            assert body["has_value"] is True, (
                f"{label} status DTO has_value should be true: {body!r}"
            )
            assert body["sensitive"] is True
        assert CLAUDE_KEY not in r_put_claude.text, (
            "PUT response leaked claude plaintext"
        )
        assert OPENAI_KEY not in r_put_openai.text, (
            "PUT response leaked openai plaintext"
        )

        # GET-list returns one row per registered key with has_value flips.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_list = c.get(f"/api/v1/teams/{team_id}/secrets")
        assert r_list.status_code == 200, (
            f"GET-list: {r_list.status_code} {r_list.text}"
        )
        list_body = r_list.json()
        assert isinstance(list_body, list) and len(list_body) >= 2
        by_key = {row["key"]: row for row in list_body}
        for key in ("claude_api_key", "openai_api_key"):
            assert key in by_key, f"missing {key!r} in list: {list_body!r}"
            assert by_key[key]["has_value"] is True, (
                f"{key} should have has_value=true: {by_key[key]!r}"
            )
            assert by_key[key]["sensitive"] is True
            assert by_key[key]["updated_at"] is not None

        # GET-single mirrors list shape.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_get_claude = c.get(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key"
            )
        assert r_get_claude.status_code == 200
        get_body = r_get_claude.json()
        assert get_body["has_value"] is True
        assert "value" not in get_body, (
            f"GET-single leaked value field: {get_body!r}"
        )
        assert CLAUDE_KEY not in r_get_claude.text

        # =====================================================================
        # Case (b) — replacing the claude key bumps updated_at
        # =====================================================================
        first_updated_at = by_key["claude_api_key"]["updated_at"]
        # Sleep ≥1s so the wall-clock-derived NOW() differs noticeably
        # (Postgres NOW() resolution is microseconds but logs render to s).
        time.sleep(1.1)
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_replace = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": CLAUDE_KEY_REPLACEMENT},
            )
        assert r_replace.status_code == 200, (
            f"replace PUT: {r_replace.status_code} {r_replace.text}"
        )
        replace_body = r_replace.json()
        assert replace_body["has_value"] is True
        assert replace_body["updated_at"] > first_updated_at, (
            "replace PUT did not bump updated_at; "
            f"first={first_updated_at!r} second={replace_body['updated_at']!r}"
        )

        # =====================================================================
        # Case (c) — DELETE clears claude; GET reports 404; list shows absence
        # =====================================================================
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_del = c.delete(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key"
            )
        assert r_del.status_code == 204, (
            f"DELETE: {r_del.status_code} {r_del.text}"
        )

        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_get_after_del = c.get(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key"
            )
            r_list_after_del = c.get(f"/api/v1/teams/{team_id}/secrets")
        assert r_get_after_del.status_code == 404, (
            f"GET after DELETE: {r_get_after_del.status_code} "
            f"{r_get_after_del.text}"
        )
        del_detail = r_get_after_del.json().get("detail") or {}
        assert del_detail.get("detail") == "team_secret_not_set", (
            f"unexpected 404 shape: {r_get_after_del.json()!r}"
        )
        list_after_del = {
            r["key"]: r for r in r_list_after_del.json()
        }
        assert list_after_del["claude_api_key"]["has_value"] is False, (
            f"claude row should report has_value=false post-delete: "
            f"{list_after_del!r}"
        )
        # OpenAI should still be present.
        assert list_after_del["openai_api_key"]["has_value"] is True

        # Re-register the claude key for the remaining cases (f)/(g).
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_reput = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": CLAUDE_KEY},
            )
        assert r_reput.status_code == 200, (
            f"re-PUT claude: {r_reput.status_code} {r_reput.text}"
        )

        # =====================================================================
        # Case (d) — non-admin member PUT → 403 team_admin_required
        # =====================================================================
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=member_cookies
        ) as c:
            r_member_put = c.put(
                f"/api/v1/teams/{team_id}/secrets/openai_api_key",
                json={"value": OPENAI_KEY},
            )
        assert r_member_put.status_code == 403, (
            f"non-admin PUT expected 403; got "
            f"{r_member_put.status_code} {r_member_put.text}"
        )
        member_detail = r_member_put.json().get("detail") or {}
        assert member_detail.get("detail") == "team_admin_required", (
            f"unexpected 403 detail: {r_member_put.json()!r}"
        )

        # Non-admin GET-list should still work (read-only gate is
        # team_member, not team_admin).
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=member_cookies
        ) as c:
            r_member_list = c.get(f"/api/v1/teams/{team_id}/secrets")
        assert r_member_list.status_code == 200

        # =====================================================================
        # Case (e) — bad-prefix value → 400 invalid_value_shape
        # =====================================================================
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_bad = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": BAD_PREFIX_VALUE},
            )
        assert r_bad.status_code == 400, (
            f"bad-prefix PUT expected 400; got {r_bad.status_code} {r_bad.text}"
        )
        bad_detail = r_bad.json().get("detail") or {}
        assert bad_detail.get("detail") == "invalid_value_shape", (
            f"bad-prefix detail unexpected: {r_bad.json()!r}"
        )
        assert bad_detail.get("key") == "claude_api_key"
        assert bad_detail.get("hint") == "bad_prefix"
        # Critical: the value itself must NOT appear in the response.
        assert BAD_PREFIX_VALUE not in r_bad.text, (
            "bad-prefix PUT echoed the offending value back"
        )

        # =====================================================================
        # Case (f) — round-trip decrypt via local-only test endpoint
        # =====================================================================
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_decrypt = c.get(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key/_test_decrypt"
            )
        assert r_decrypt.status_code == 200, (
            f"_test_decrypt: {r_decrypt.status_code} {r_decrypt.text}"
        )
        round_trip = r_decrypt.json()
        assert round_trip["key"] == "claude_api_key"
        assert round_trip["value"] == CLAUDE_KEY, (
            "round-trip plaintext mismatch — encrypt/decrypt asymmetry"
        )

        # The non-admin (regular user, not system_admin) must be 403'd
        # by the test endpoint itself even within `local`. The member
        # added above is also not a system_admin.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=member_cookies
        ) as c:
            r_member_decrypt = c.get(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key/_test_decrypt"
            )
        assert r_member_decrypt.status_code == 403, (
            "non-system_admin called the local-only test-decrypt and "
            "did not get 403; got "
            f"{r_member_decrypt.status_code} {r_member_decrypt.text}"
        )

        # =====================================================================
        # Case (g) — corrupt ciphertext → 503 team_secret_decrypt_failed
        # =====================================================================
        corrupt_sql = (
            "UPDATE team_secrets "
            "SET value_encrypted = E'\\\\xdeadbeef' "
            f"WHERE team_id='{team_id}' AND key='claude_api_key'"
        )
        upd = _psql_exec(corrupt_sql)
        assert upd.returncode == 0, (
            f"psql UPDATE failed; rc={upd.returncode} stderr={upd.stderr!r}"
        )
        corrupted_len = _psql_one(
            "SELECT length(value_encrypted) FROM team_secrets "
            f"WHERE team_id='{team_id}' AND key='claude_api_key'"
        )
        assert corrupted_len == "4", (
            f"corrupted ciphertext should be 4 bytes; got {corrupted_len!r}"
        )

        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r_tamper = c.get(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key/_test_decrypt"
            )
        assert r_tamper.status_code == 503, (
            f"tamper expected 503; got {r_tamper.status_code} {r_tamper.text}"
        )
        tamper_body = r_tamper.json()
        assert tamper_body["detail"] == "team_secret_decrypt_failed", (
            f"tamper detail unexpected: {tamper_body!r}"
        )
        assert tamper_body["key"] == "claude_api_key"

        time.sleep(1.0)
        backend_log_after_tamper = _backend_logs(backend_container)
        expected_decrypt_line = (
            f"team_secret_decrypt_failed team_id={team_id} "
            "key=claude_api_key"
        )
        assert expected_decrypt_line in backend_log_after_tamper, (
            f"missing {expected_decrypt_line!r} in backend logs; tail:\n"
            f"{backend_log_after_tamper[-2000:]}"
        )

        # =====================================================================
        # Case (h) — final redaction sweep over backend container logs
        # =====================================================================
        # Slice plan locks: zero `sk-ant-` and zero `sk-...` (≥20-char
        # bearer-shaped) matches in the backend container logs after the
        # full e2e run. Sentinels in the synthetic keys make any hit
        # unambiguously a leak from THIS run.
        backend_log_final = _backend_logs(backend_container)

        for sentinel, label in (
            (CLAUDE_KEY, "first claude key plaintext"),
            (CLAUDE_KEY_REPLACEMENT, "replacement claude key plaintext"),
            (OPENAI_KEY, "openai key plaintext"),
            (BAD_PREFIX_VALUE, "bad-prefix probe value"),
        ):
            assert sentinel not in backend_log_final, (
                f"redaction sweep: {label} leaked into backend logs"
            )

        # Defense in depth — any `sk-ant-` or bearer-shape `sk-` substring
        # at all is a regression. The slice's INFO log lines never carry
        # the value, only team_id + key.
        sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", backend_log_final)
        assert sk_ant_hits == [], (
            f"redaction sweep: backend logs contain `sk-ant-` matches: "
            f"{sk_ant_hits[:3]!r}"
        )
        # `sk-` followed by ≥20 url-safe chars catches OpenAI shapes
        # without false-positiving on innocuous strings like "sk-skip".
        sk_hits = re.findall(r"sk-[A-Za-z0-9_-]{20,}", backend_log_final)
        assert sk_hits == [], (
            f"redaction sweep: backend logs contain bearer-shape `sk-` "
            f"matches: {sk_hits[:3]!r}"
        )

        # Smoke: the slice's full INFO/ERROR taxonomy fired.
        for marker in (
            "team_secret_set",
            "team_secret_deleted",
            "team_secret_decrypt_failed",
        ):
            assert marker in backend_log_final, (
                f"observability taxonomy regression: {marker!r} not seen "
                "in backend logs"
            )

    finally:
        # Cleanup — drop test rows so re-runs start clean and so other
        # tests sharing the persistent compose volume don't inherit stale
        # state.
        _delete_team_secrets_for(team_id)
        _delete_team_and_members(team_id)
        _delete_user_by_email(member_email)

    elapsed = time.time() - suite_started
    # Slice budget is ≤30 s; tolerate up to 90 s defensively because
    # `docker exec` cold-imports cost a few seconds on slow hosts.
    assert elapsed < 90.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 30s slice budget"
    )
