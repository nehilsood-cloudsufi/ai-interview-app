"""Hand-rolled in-memory fakes for external SDKs used in tests.

`google.cloud.storage` is imported locally inside `transcript_store.py`'s GCS
functions, so tests patch `google.cloud.storage.Client` with these fakes
rather than pulling in a real GCS emulator dependency.
"""

from __future__ import annotations


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str) -> None:
        self._bucket = bucket
        self.name = name

    def upload_from_string(self, data: str, content_type: str | None = None) -> None:
        if self._bucket.raise_on_upload is not None:
            raise self._bucket.raise_on_upload
        self._bucket.store[self.name] = data

    def exists(self) -> bool:
        return self.name in self._bucket.store

    def download_as_text(self) -> str:
        if self._bucket.raise_on_download is not None:
            raise self._bucket.raise_on_download
        return self._bucket.store[self.name]


class FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.store: dict[str, str] = {}
        # Set to an exception instance to make blob ops on this bucket raise,
        # exercising the store's GCS error-propagation contract.
        self.raise_on_upload: Exception | None = None
        self.raise_on_download: Exception | None = None

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        if name not in self.buckets:
            self.buckets[name] = FakeBucket(name)
        return self.buckets[name]
