"""Publish only screenshots captured inside this worker's evidence directory."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

from src.operator.channels.base import ChannelError
from src.operator.channels.web import HttpSink

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def evidence_body(directory: Path, filename: str, job_id: str) -> dict:
    """Read a contained image with a bounded read; the server also validates its magic bytes."""
    path = Path(filename).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ChannelError("evidence_outside_directory")
    mime = MIMES.get(path.suffix.lower())
    if mime is None:
        raise ChannelError("unsupported_media_type")
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise ChannelError("evidence_unreadable") from None
    if len(content) > MAX_IMAGE_BYTES:
        raise ChannelError("too_large")
    return {"job_id": job_id, "name": path.name, "mime": mime,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_b64": base64.b64encode(content).decode("ascii")}


async def upload_screenshots(sink: HttpSink, directory: Path, run_id: str,
                             job_id: str, filenames: list[str]) -> None:
    """Finish idempotent authenticated uploads before advertising a review page."""
    for filename in filenames:
        body = await asyncio.to_thread(evidence_body, directory, filename, job_id)
        await sink.post_evidence(run_id, body)
