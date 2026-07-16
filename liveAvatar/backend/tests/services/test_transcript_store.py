import json
import os

import pytest

from app.services import transcript_store


async def test_local_save_and_get_roundtrip(tmp_transcripts_dir):
    payload = {"session_id": "s1", "turns": [{"role": "candidate", "text": "hi"}]}
    await transcript_store.save("s1", payload)

    result = await transcript_store.get("s1")
    assert result == payload


async def test_local_save_creates_directory(tmp_transcripts_dir):
    assert not os.path.isdir(tmp_transcripts_dir)
    await transcript_store.save("s1", {"a": 1})
    assert os.path.isdir(tmp_transcripts_dir)
    assert os.path.exists(os.path.join(tmp_transcripts_dir, "s1.json"))


async def test_local_get_missing_file_returns_none(tmp_transcripts_dir):
    result = await transcript_store.get("does-not-exist")
    assert result is None


async def test_local_save_writes_indented_json(tmp_transcripts_dir):
    await transcript_store.save("s1", {"a": 1})
    path = os.path.join(tmp_transcripts_dir, "s1.json")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    assert "\n" in raw  # indent=2 formatting
    assert json.loads(raw) == {"a": 1}


async def test_gcs_save_and_get_roundtrip(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    payload = {"session_id": "s1", "turns": []}
    await transcript_store.save("s1", payload)

    result = await transcript_store.get("s1")
    assert result == payload

    bucket = fake_gcs_client.buckets["my-bucket"]
    assert "transcripts/s1.json" in bucket.store


async def test_gcs_get_missing_blob_returns_none(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    result = await transcript_store.get("missing-session")
    assert result is None


async def test_dispatches_to_local_when_bucket_falsy(patch_settings, fake_gcs_client, tmp_path):
    directory = tmp_path / "local-transcripts"
    patch_settings(gcs_bucket=None, transcripts_local_dir=str(directory))

    await transcript_store.save("s1", {"a": 1})

    # Nothing should have gone to the fake GCS client.
    assert fake_gcs_client.buckets == {}
    assert os.path.exists(os.path.join(directory, "s1.json"))


async def test_dispatches_to_gcs_when_bucket_set(patch_settings, fake_gcs_client, tmp_path):
    directory = tmp_path / "local-transcripts"
    patch_settings(gcs_bucket="my-bucket", transcripts_local_dir=str(directory))

    await transcript_store.save("s1", {"a": 1})

    assert "my-bucket" in fake_gcs_client.buckets
    assert not os.path.exists(directory)


async def test_gcs_save_error_propagates(patch_settings, fake_gcs_client):
    # The store has no try/except - GCS failures must bubble up so the router
    # can turn them into a 500 rather than silently losing the transcript.
    patch_settings(gcs_bucket="my-bucket")
    fake_gcs_client.bucket("my-bucket").raise_on_upload = RuntimeError("gcs down")

    with pytest.raises(RuntimeError, match="gcs down"):
        await transcript_store.save("s1", {"a": 1})


async def test_gcs_get_error_propagates(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    # Blob must exist() for the download path to be reached.
    await transcript_store.save("s1", {"a": 1})
    fake_gcs_client.bucket("my-bucket").raise_on_download = RuntimeError("gcs read fail")

    with pytest.raises(RuntimeError, match="gcs read fail"):
        await transcript_store.get("s1")
