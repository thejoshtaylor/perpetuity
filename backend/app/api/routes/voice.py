"""Authenticated voice transcription proxy."""

from __future__ import annotations

import logging
from typing import Protocol

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from app.api.deps import CurrentUser, SessionDep
from app.core.grok_stt import transcribe_audio
from app.core.rate_limit import RateLimitDecision, RedisSlidingWindowRateLimiter
from app.models import VoiceTranscribeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

_ALLOWED_AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/wav",
        "audio/wave",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
    }
)
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_RATE_LIMIT = 30
_RATE_WINDOW_SECONDS = 60


class VoiceRateLimiter(Protocol):
    async def check(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision: ...


_rate_limiter: VoiceRateLimiter = RedisSlidingWindowRateLimiter()


def set_voice_rate_limiter(limiter: VoiceRateLimiter) -> None:
    """Test injection seam for limiter replacement."""
    global _rate_limiter
    _rate_limiter = limiter


async def _read_valid_audio(file: UploadFile) -> tuple[bytes, str]:
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"detail": "voice_unsupported_content_type"},
        )
    audio = await file.read(_MAX_AUDIO_BYTES + 1)
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"detail": "voice_audio_empty"},
        )
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"detail": "voice_audio_too_large"},
        )
    filename = file.filename or "recording.webm"
    return audio, filename


@router.post("/transcribe", response_model=VoiceTranscribeResponse)
async def transcribe_voice(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    response: Response,
    file: UploadFile = File(...),
) -> VoiceTranscribeResponse:
    """Validate upload, enforce per-user rate limit, proxy to Grok STT."""
    audio, filename = await _read_valid_audio(file)
    mime = file.content_type or ""
    logger.info(
        "voice.transcribe.start user_id=%s mime=%s bytes=%s",
        current_user.id,
        mime,
        len(audio),
    )

    limit_key = f"voice:transcribe:{current_user.id}"
    try:
        decision = await _rate_limiter.check(
            limit_key, limit=_RATE_LIMIT, window_seconds=_RATE_WINDOW_SECONDS
        )
    except Exception:
        logger.error(
            "voice.transcribe.rate_limit_failed user_id=%s mime=%s bytes=%s",
            current_user.id,
            mime,
            len(audio),
        )
        raise HTTPException(
            status_code=503,
            detail={"detail": "voice_rate_limit_unavailable"},
        )

    if not decision.allowed:
        response.headers["Retry-After"] = str(decision.retry_after)
        logger.info(
            "voice.transcribe.rate_limited user_id=%s mime=%s bytes=%s retry_after=%s",
            current_user.id,
            mime,
            len(audio),
            decision.retry_after,
        )
        raise HTTPException(
            status_code=429,
            detail={"detail": "voice_rate_limited"},
            headers={"Retry-After": str(decision.retry_after)},
        )

    result = await transcribe_audio(
        session,
        audio=audio,
        filename=filename,
        content_type=mime,
    )
    logger.info(
        "voice.transcribe.success user_id=%s mime=%s bytes=%s",
        current_user.id,
        mime,
        len(audio),
    )
    return VoiceTranscribeResponse(text=result.text)
