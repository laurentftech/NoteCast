"""Audio transcription via faster-whisper."""
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_model = None
_model_size: Optional[str] = None


def _get_model(model_size: str):
    global _model, _model_size
    if _model is None or _model_size != model_size:
        from faster_whisper import WhisperModel
        logger.info("Loading Whisper model: %s", model_size)
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _model_size = model_size
        logger.info("Whisper model loaded")
    return _model


async def transcribe_url(audio_url: str, model_size: str = "base") -> Path:
    """Download audio from URL and transcribe with Whisper.

    Returns path to a temporary .txt file with the transcript.
    Caller is responsible for deleting the file.
    """
    logger.info("Downloading audio for transcription: %s", audio_url[:80])
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.get(audio_url)
        resp.raise_for_status()

    suffix = Path(audio_url.split("?")[0]).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as audio_f:
        audio_path = Path(audio_f.name)
        audio_path.write_bytes(resp.content)

    logger.info("Transcribing %s (%.1f MB)", audio_path.name, len(resp.content) / 1e6)
    try:
        model = _get_model(model_size)
        segments, info = model.transcribe(str(audio_path), beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            "Transcription done: %.0fs audio, %d chars",
            info.duration, len(text),
        )
    finally:
        audio_path.unlink(missing_ok=True)

    with tempfile.NamedTemporaryFile(
        suffix=".txt", mode="w", encoding="utf-8", delete=False
    ) as txt_f:
        txt_f.write(text)
        return Path(txt_f.name)
