"""Voice transcription route tests (M005/S04/T01)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.api.routes import voice as voice_route
from app.core import grok_stt
from app.core.config import settings
from app.core.rate_limit import RateLimitDecision
from app.models import SystemSetting
from tests.utils.utils import random_email, random_lower_string

API_V1 = settings.API_V1_STR
VOICE_URL = f"{API_V1}/voice/transcribe"
ADMIN_SETTINGS_URL = f"{API_V1}/admin/settings"
SIGNUP_URL = f"{API_V1}/auth/signup"
GROK_KEY = "grok_stt_api_key"
SECRET_VALUE = "xai-secret-never-log"
TRANSCRIPT_VALUE = "dictated text never log"


@pytest.fixture(autouse=True)
def _clean_system_settings(db: Session):
    db.execute(delete(SystemSetting))
    db.commit()
    yield
    db.execute(delete(SystemSetting))
    db.commit()


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "SYSTEM_SETTINGS_ENCRYPTION_KEY",
        "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo=",
    )
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


@pytest.fixture(autouse=True)
def _restore_route_globals():
    original_limiter = voice_route._rate_limiter
    original_max_bytes = voice_route._MAX_AUDIO_BYTES
    yield
    voice_route.set_voice_rate_limiter(original_limiter)
    voice_route._MAX_AUDIO_BYTES = original_max_bytes


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    jar = httpx.Cookies()
    for cookie in client.cookies.jar:
        jar.set(cookie.name, cookie.value)
    client.cookies.clear()
    return r.json()["id"], jar


def _audio_file(
    *, content: bytes = b"abc", content_type: str = "audio/webm"
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("sample.webm", content, content_type)}


class AllowLimiter:
    async def check(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, retry_after=0, remaining=limit - 1)


class CountingLimiter:
    def __init__(self) -> None:
        self.count = 0

    async def check(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        self.count += 1
        if self.count > limit:
            return RateLimitDecision(allowed=False, retry_after=17, remaining=0)
        return RateLimitDecision(allowed=True, retry_after=0, remaining=limit - self.count)


class BoomLimiter:
    async def check(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        raise RuntimeError("redis down")


async def _fake_transcribe_success(
    _session: Session, *, _audio: bytes, _filename: str, _content_type: str
) -> grok_stt.GrokSttResult:
    return grok_stt.GrokSttResult(text=TRANSCRIPT_VALUE)


def _patch_transcriber(
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[..., Awaitable[grok_stt.GrokSttResult]],
) -> None:
    monkeypatch.setattr(voice_route, "transcribe_audio", fn)


def test_voice_transcribe_requires_auth(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(VOICE_URL, files=_audio_file())
    assert r.status_code == 401


def test_voice_transcribe_happy_path_returns_text_and_redacts_logs(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    voice_route.set_voice_rate_limiter(AllowLimiter())
    _patch_transcriber(monkeypatch, _fake_transcribe_success)

    with caplog.at_level(logging.INFO, logger="app.api.routes.voice"):
        r = client.post(
            VOICE_URL,
            cookies=normal_user_cookies,
            files=_audio_file(content=b"audio-bytes"),
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"text": TRANSCRIPT_VALUE}

    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "voice.transcribe.start" in logs
    assert "voice.transcribe.success" in logs
    assert "audio-bytes" not in logs
    assert TRANSCRIPT_VALUE not in logs
    assert SECRET_VALUE not in logs
    assert "multipart" not in logs.lower()


@pytest.mark.parametrize(
    ("files", "expected_status", "detail"),
    [
        ({}, 422, None),
        (_audio_file(content_type="text/plain"), 415, "voice_unsupported_content_type"),
        (_audio_file(content=b""), 422, "voice_audio_empty"),
    ],
)
def test_voice_transcribe_rejects_malformed_inputs_before_grok(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, tuple[str, bytes, str]],
    expected_status: int,
    detail: str | None,
) -> None:
    async def _should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("grok should not be called")

    voice_route.set_voice_rate_limiter(AllowLimiter())
    _patch_transcriber(monkeypatch, _should_not_call)
    r = client.post(VOICE_URL, cookies=normal_user_cookies, files=files)
    assert r.status_code == expected_status, r.text
    if detail is not None:
        assert r.json()["detail"]["detail"] == detail


def test_voice_transcribe_rejects_oversized_upload_before_grok(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("grok should not be called")

    voice_route._MAX_AUDIO_BYTES = 4
    voice_route.set_voice_rate_limiter(AllowLimiter())
    _patch_transcriber(monkeypatch, _should_not_call)
    r = client.post(
        VOICE_URL,
        cookies=normal_user_cookies,
        files=_audio_file(content=b"12345"),
    )
    assert r.status_code == 413, r.text
    assert r.json()["detail"]["detail"] == "voice_audio_too_large"


def test_voice_transcribe_rate_limit_31st_returns_retry_after(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = CountingLimiter()
    voice_route.set_voice_rate_limiter(limiter)
    _patch_transcriber(monkeypatch, _fake_transcribe_success)

    for _ in range(30):
        r = client.post(VOICE_URL, cookies=normal_user_cookies, files=_audio_file())
        assert r.status_code == 200, r.text

    r = client.post(VOICE_URL, cookies=normal_user_cookies, files=_audio_file())
    assert r.status_code == 429, r.text
    assert r.headers["Retry-After"] == "17"
    assert r.json()["detail"]["detail"] == "voice_rate_limited"


def test_voice_transcribe_rate_limit_unavailable_returns_503(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("grok should not be called")

    voice_route.set_voice_rate_limiter(BoomLimiter())
    _patch_transcriber(monkeypatch, _should_not_call)

    with caplog.at_level(logging.ERROR, logger="app.api.routes.voice"):
        r = client.post(VOICE_URL, cookies=normal_user_cookies, files=_audio_file())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["detail"] == "voice_rate_limit_unavailable"
    assert any("voice.transcribe.rate_limit_failed" in rec.getMessage() for rec in caplog.records)


def test_voice_transcribe_missing_key_returns_503(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
) -> None:
    voice_route.set_voice_rate_limiter(AllowLimiter())
    r = client.post(VOICE_URL, cookies=normal_user_cookies, files=_audio_file())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["detail"] == "grok_stt_api_key_not_configured"


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (502, "voice_transcribe_failed"),
        (504, "voice_transcribe_timeout"),
        (502, "voice_transcribe_bad_response"),
    ],
)
def test_voice_transcribe_surfaces_upstream_failures(
    client: TestClient,
    normal_user_cookies: httpx.Cookies,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    detail: str,
) -> None:
    async def _fake_failure(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise HTTPException(status_code=status_code, detail={"detail": detail})

    voice_route.set_voice_rate_limiter(AllowLimiter())
    _patch_transcriber(monkeypatch, _fake_failure)
    r = client.post(VOICE_URL, cookies=normal_user_cookies, files=_audio_file())
    assert r.status_code == status_code, r.text
    assert r.json()["detail"]["detail"] == detail


def test_grok_key_stored_encrypted_and_transcribe_never_logs_key_or_text(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    normal_user_cookies: httpx.Cookies,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    put = client.put(
        f"{ADMIN_SETTINGS_URL}/{GROK_KEY}",
        cookies=superuser_cookies,
        json={"value": SECRET_VALUE},
    )
    assert put.status_code == 200, put.text
    assert put.json()["value"] is None
    row = db.exec(select(SystemSetting).where(SystemSetting.key == GROK_KEY)).one()
    assert row.sensitive is True
    assert row.value is None
    assert row.value_encrypted is not None
    assert SECRET_VALUE.encode() not in row.value_encrypted

    _patch_transcriber(monkeypatch, _fake_transcribe_success)
    voice_route.set_voice_rate_limiter(AllowLimiter())
    with caplog.at_level(logging.INFO, logger="app.api.routes.voice"):
        r = client.post(
            VOICE_URL,
            cookies=normal_user_cookies,
            files=_audio_file(content=b"voice-bytes"),
        )
    assert r.status_code == 200, r.text
    combined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert SECRET_VALUE not in combined
    assert TRANSCRIPT_VALUE not in combined
    assert "voice-bytes" not in combined
