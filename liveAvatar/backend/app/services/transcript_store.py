"""Persistence for finalized interview records, keyed by HeyGen session id.

One backend switch decides where records live: if `settings.gcs_bucket` is set,
records go to Google Cloud Storage as the blob `transcripts/{session_id}.json`;
otherwise they fall back to local JSON files under `settings.transcripts_local_dir`
(the gitignored dev path). The `google.cloud.storage` import is done lazily
inside the GCS helpers so the dependency is only needed when GCS is actually
configured, and the synchronous SDK/file work is pushed onto a thread via
`asyncio.to_thread` to keep the event loop free.

There is deliberately no try/except here: any failure propagates to the caller.
The `transcripts` router turns a save failure into a 500, and `pipeline.py`
catches its own save failures - keeping the error policy with the callers that
know how to recover rather than swallowing it at this layer."""

import asyncio
import json
import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

_BLOB_PREFIX = "transcripts"


def _blob_name(session_id: str) -> str:
    """The GCS blob name for a session's record (`transcripts/{session_id}.json`)."""
    return f"{_BLOB_PREFIX}/{session_id}.json"


def _local_path(session_id: str) -> str:
    """The local filesystem path for a session's record in dev-fallback mode."""
    return os.path.join(settings.transcripts_local_dir, f"{session_id}.json")


# --- GCS (sync SDK, wrapped in a thread) ---

def _gcs_save(session_id: str, body: str) -> None:
    """Upload the record JSON to GCS (blocking; run in a thread by `save`)."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(session_id))
    blob.upload_from_string(body, content_type="application/json")


def _gcs_get(session_id: str) -> dict | None:
    """Load a record from GCS, or None if the blob does not exist (blocking;
    run in a thread by `get`)."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(session_id))
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


# --- Local files (dev fallback) ---

def _local_save(session_id: str, body: str) -> None:
    """Write the record JSON to a local file, creating the dir if needed
    (blocking; run in a thread by `save`)."""
    os.makedirs(settings.transcripts_local_dir, exist_ok=True)
    with open(_local_path(session_id), "w", encoding="utf-8") as f:
        f.write(body)


def _local_get(session_id: str) -> dict | None:
    """Load a record from a local file, or None if it does not exist (blocking;
    run in a thread by `get`)."""
    path = _local_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def save(session_id: str, payload: dict) -> None:
    """Persist a finalized interview record under its session id.

    Serializes `payload` to pretty JSON and writes it to GCS or a local file
    depending on whether `settings.gcs_bucket` is set, offloading the blocking
    write to a thread. Raises on any backend failure (no swallowing here) so
    the caller can decide how to recover."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if settings.gcs_bucket:
        await asyncio.to_thread(_gcs_save, session_id, body)
        logger.info("Saved transcript %s to gs://%s/%s", session_id, settings.gcs_bucket, _blob_name(session_id))
    else:
        await asyncio.to_thread(_local_save, session_id, body)
        logger.info("Saved transcript %s to %s", session_id, _local_path(session_id))


async def get(session_id: str) -> dict | None:
    """Read a saved interview record back by session id, or None if there is no
    record. Reads from GCS or a local file per `settings.gcs_bucket`, offloading
    the blocking read to a thread; backend errors propagate to the caller."""
    if settings.gcs_bucket:
        return await asyncio.to_thread(_gcs_get, session_id)
    return await asyncio.to_thread(_local_get, session_id)
