import json
import os

import pytest

from app.services import scout_store


async def test_local_save_and_get_roundtrip(tmp_scout_dir):
    payload = {"scout_id": "sc1", "findings": "report"}
    await scout_store.save("sc1", payload)

    result = await scout_store.get("sc1")
    assert result == payload


async def test_local_save_creates_directory(tmp_scout_dir):
    assert not os.path.isdir(tmp_scout_dir)
    await scout_store.save("sc1", {"a": 1})
    assert os.path.isdir(tmp_scout_dir)
    assert os.path.exists(os.path.join(tmp_scout_dir, "sc1.json"))


async def test_local_get_missing_file_returns_none(tmp_scout_dir):
    result = await scout_store.get("does-not-exist")
    assert result is None


async def test_gcs_save_and_get_roundtrip(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    payload = {"scout_id": "sc1", "findings": "report"}
    await scout_store.save("sc1", payload)

    result = await scout_store.get("sc1")
    assert result == payload

    bucket = fake_gcs_client.buckets["my-bucket"]
    assert "scout/sc1.json" in bucket.store


async def test_gcs_get_missing_blob_returns_none(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    result = await scout_store.get("missing-scout")
    assert result is None


async def test_dispatches_to_local_when_bucket_falsy(patch_settings, fake_gcs_client, tmp_path):
    directory = tmp_path / "local-scout"
    patch_settings(gcs_bucket=None, scout_local_dir=str(directory))

    await scout_store.save("sc1", {"a": 1})

    assert fake_gcs_client.buckets == {}
    assert os.path.exists(os.path.join(directory, "sc1.json"))


async def test_gcs_save_error_propagates(patch_settings, fake_gcs_client):
    patch_settings(gcs_bucket="my-bucket")
    fake_gcs_client.bucket("my-bucket").raise_on_upload = RuntimeError("gcs down")

    with pytest.raises(RuntimeError, match="gcs down"):
        await scout_store.save("sc1", {"a": 1})
