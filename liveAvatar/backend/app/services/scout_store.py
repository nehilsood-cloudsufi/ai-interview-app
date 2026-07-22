import asyncio
import json
import logging
import os
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_BLOB_PREFIX = "scout"  # GCS blob path prefix; final path is "{_BLOB_PREFIX}/{scout_id}.json"


def _blob_name(scout_id: str) -> str:
    """Returns the GCS blob path for a given scout report id."""
    return f"{_BLOB_PREFIX}/{scout_id}.json"


def _local_path(scout_id: str) -> str:
    """Returns the local filesystem path for a given scout report id."""
    return os.path.join(settings.scout_local_dir, f"{scout_id}.json")


# --- GCS (sync SDK, wrapped in a thread) ---
# google.cloud.storage is imported lazily inside each function (not at module
# level) so importing this module never requires the dependency to be
# installed/configured when GCS_BUCKET is unset.

def _gcs_save(scout_id: str, body: str) -> None:
    """Uploads a scout report's serialized JSON body to GCS."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(scout_id))
    blob.upload_from_string(body, content_type="application/json")


def _gcs_get(scout_id: str) -> dict[str, Any] | None:
    """Downloads and parses a scout report's JSON from GCS, or None if absent."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(_blob_name(scout_id))
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


# --- Local files (dev fallback) ---

def _local_save(scout_id: str, body: str) -> None:
    """Writes a scout report's serialized JSON body to a local file."""
    os.makedirs(settings.scout_local_dir, exist_ok=True)
    with open(_local_path(scout_id), "w", encoding="utf-8") as file:
        file.write(body)


def _local_get(scout_id: str) -> dict[str, Any] | None:
    """Reads and parses a scout report's JSON from a local file, or None if absent."""
    path = _local_path(scout_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        return json.load(file)


async def save(scout_id: str, payload: dict[str, Any]) -> None:
    """Persists a scout report: to GCS when GCS_BUCKET is set, otherwise to a
    local JSON file under scout_local_dir. No return value."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if settings.gcs_bucket:
        await asyncio.to_thread(_gcs_save, scout_id, body)
        logger.info("Saved scout report %s to gs://%s/%s", scout_id, settings.gcs_bucket, _blob_name(scout_id))
    else:
        await asyncio.to_thread(_local_save, scout_id, body)
        logger.info("Saved scout report %s to %s", scout_id, _local_path(scout_id))


async def get(scout_id: str) -> dict[str, Any] | None:
    """Retrieves a previously saved scout report by id, from GCS or local
    storage depending on configuration. Returns None if no report exists for
    that id."""
    if settings.gcs_bucket:
        return await asyncio.to_thread(_gcs_get, scout_id)
    return await asyncio.to_thread(_local_get, scout_id)
