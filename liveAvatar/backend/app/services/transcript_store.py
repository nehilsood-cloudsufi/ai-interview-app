import asyncio
import json
import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

_BLOB_PREFIX = "transcripts"


def _blob_name(session_id: str) -> str:
    return f"{_BLOB_PREFIX}/{session_id}.json"


def _local_path(session_id: str) -> str:
    return os.path.join(settings.transcripts_local_dir, f"{session_id}.json")


# --- GCS (sync SDK, wrapped in a thread) ---

def _gcs_save(session_id: str, body: str) -> None:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(session_id))
    blob.upload_from_string(body, content_type="application/json")


def _gcs_get(session_id: str) -> dict | None:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(session_id))
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


# --- Local files (dev fallback) ---

def _local_save(session_id: str, body: str) -> None:
    os.makedirs(settings.transcripts_local_dir, exist_ok=True)
    with open(_local_path(session_id), "w", encoding="utf-8") as f:
        f.write(body)


def _local_get(session_id: str) -> dict | None:
    path = _local_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def save(session_id: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if settings.gcs_bucket:
        await asyncio.to_thread(_gcs_save, session_id, body)
        logger.info("Saved transcript %s to gs://%s/%s", session_id, settings.gcs_bucket, _blob_name(session_id))
    else:
        await asyncio.to_thread(_local_save, session_id, body)
        logger.info("Saved transcript %s to %s", session_id, _local_path(session_id))


async def get(session_id: str) -> dict | None:
    if settings.gcs_bucket:
        return await asyncio.to_thread(_gcs_get, session_id)
    return await asyncio.to_thread(_local_get, session_id)
