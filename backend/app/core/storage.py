"""Private object storage for exports and permitted image copies."""

import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings


class ObjectStorage(Protocol):
    def put_file(self, key: str, source: Path, content_type: str) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("Invalid storage key")
        return path

    def put_file(self, key: str, source: Path, content_type: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:  # pragma: no cover - requires an S3-compatible endpoint
    def __init__(self, bucket: str, endpoint_url: str | None, region: str | None):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def put_file(self, key: str, source: Path, content_type: str) -> None:
        self.client.upload_file(
            str(source), self.bucket, key, ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"}
        )

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend == "s3":
            assert settings.s3_bucket, "MPI_S3_BUCKET is required for the s3 storage backend"
            _storage = S3Storage(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_region)
        else:
            _storage = LocalStorage(settings.storage_dir)
    return _storage
