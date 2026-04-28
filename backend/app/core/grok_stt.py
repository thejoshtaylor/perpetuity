"""Grok/xAI speech-to-text client with redacted diagnostics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from sqlmodel import Session

from app.core.encryption import SystemSettingDecryptError, decrypt_setting
from app.models import SystemSetting

logger = logging.getLogger(__name__)

GROK_STT_API_KEY = "grok_stt_api_key"
GROK_STT_URL = "https://api.x.ai/v1/audio/transcriptions"
GROK_STT_MODEL = "grok-voice"
GROK_STT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GrokSttResult:
    text: str


def _load_grok_key(session: Session) -> str:
    row = session.get(SystemSetting, GROK_STT_API_KEY)
    if row is None or not row.has_value or row.value_encrypted is None:
        logger.error("voice.transcribe.failed reason=missing_key")
        raise HTTPException(
            status_code=503,
            detail={"detail": "grok_stt_api_key_not_configured"},
        )
    try:
        return decrypt_setting(bytes(row.value_encrypted))
    except SystemSettingDecryptError as exc:
        raise SystemSettingDecryptError(key=GROK_STT_API_KEY) from exc


def _normalize_response(payload: Any) -> GrokSttResult:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={"detail": "voice_transcribe_bad_response"},
        )
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(
            status_code=502,
            detail={"detail": "voice_transcribe_bad_response"},
        )
    return GrokSttResult(text=text)


async def transcribe_audio(
    session: Session,
    *,
    audio: bytes,
    filename: str,
    content_type: str,
) -> GrokSttResult:
    """Decrypt Grok key at call-site, post multipart audio, return text.

    Logs include status class and byte count only. API key, raw audio,
    multipart boundary, and transcript text never appear in log messages.
    """
    api_key = _load_grok_key(session)
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, audio, content_type)}
    data = {"model": GROK_STT_MODEL}
    try:
        async with httpx.AsyncClient(timeout=GROK_STT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                GROK_STT_URL,
                headers=headers,
                data=data,
                files=files,
            )
    except httpx.TimeoutException:
        logger.warning(
            "voice.transcribe.failed reason=timeout mime=%s bytes=%s",
            content_type,
            len(audio),
        )
        raise HTTPException(
            status_code=504,
            detail={"detail": "voice_transcribe_timeout"},
        )
    except httpx.HTTPError:
        logger.warning(
            "voice.transcribe.failed reason=transport mime=%s bytes=%s",
            content_type,
            len(audio),
        )
        raise HTTPException(
            status_code=502,
            detail={"detail": "voice_transcribe_failed"},
        )

    status_class = response.status_code // 100
    if response.status_code >= 400:
        logger.warning(
            "voice.transcribe.failed reason=upstream_status status_class=%sxx mime=%s bytes=%s",
            status_class,
            content_type,
            len(audio),
        )
        raise HTTPException(
            status_code=502,
            detail={"detail": "voice_transcribe_failed"},
        )

    try:
        payload = response.json()
    except ValueError:
        logger.warning(
            "voice.transcribe.failed reason=bad_json status_class=%sxx mime=%s bytes=%s",
            status_class,
            content_type,
            len(audio),
        )
        raise HTTPException(
            status_code=502,
            detail={"detail": "voice_transcribe_bad_response"},
        )

    try:
        return _normalize_response(payload)
    except HTTPException:
        logger.warning(
            "voice.transcribe.failed reason=bad_shape status_class=%sxx mime=%s bytes=%s",
            status_class,
            content_type,
            len(audio),
        )
        raise
