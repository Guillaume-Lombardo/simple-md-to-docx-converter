"""Provider-neutral object storage ports and profile adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import mkstemp
from typing import Any, Protocol
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError


class ObjectScope(StrEnum):
    """Fixed object namespaces; visible names never enter storage keys."""

    UPLOAD = "uploads"
    RESULT = "results"
    RESULT_MANIFEST = "result-manifests"
    TEMPLATE_VERSION = "template-versions"


@dataclass(frozen=True, slots=True)
class ObjectKey:
    """Object location derived exclusively from stable identifiers."""

    scope: ObjectScope
    owner_id: UUID
    object_id: UUID

    def as_posix(self) -> str:
        return f"{self.scope.value}/{self.owner_id}/{self.object_id}"


class ObjectNotFoundError(LookupError):
    """Requested stable object identifier does not exist."""


class ObjectStoreError(RuntimeError):
    """Sanitized object-store boundary failure."""


class ObjectStore(Protocol):
    """Atomic object contract shared by both runtime profiles."""

    def put(self, key: ObjectKey, content: bytes) -> None: ...

    def get(self, key: ObjectKey) -> bytes: ...

    def delete(self, key: ObjectKey) -> None: ...

    def exists(self, key: ObjectKey) -> bool: ...

    def is_ready(self) -> bool: ...


class FilesystemObjectStore:
    """Atomic object storage rooted below the standalone `/data` PVC."""

    def __init__(self, data_directory: Path) -> None:
        self._root = data_directory / "objects"
        try:
            self._ensure_directory(self._root)
        except OSError:
            raise ObjectStoreError("Object storage operation failed") from None

    def put(self, key: ObjectKey, content: bytes) -> None:
        target = self._path(key)
        temporary: Path | None = None
        try:
            self._ensure_directory(target.parent)
            descriptor, temporary_name = mkstemp(prefix=".pending-", dir=target.parent)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            self._sync_directory(target.parent)
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ObjectStoreError("Object storage operation failed") from None

    def get(self, key: ObjectKey) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError("Object does not exist") from None
        except OSError:
            raise ObjectStoreError("Object storage operation failed") from None

    def delete(self, key: ObjectKey) -> None:
        target = self._path(key)
        try:
            target.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise ObjectStoreError("Object storage operation failed") from None
        try:
            self._sync_directory(target.parent)
        except OSError:
            raise ObjectStoreError("Object storage operation failed") from None

    def exists(self, key: ObjectKey) -> bool:
        try:
            return self._path(key).is_file()
        except OSError:
            raise ObjectStoreError("Object storage operation failed") from None

    def is_ready(self) -> bool:
        return self._root.is_dir() and os.access(
            self._root, os.R_OK | os.W_OK | os.X_OK
        )

    def _path(self, key: ObjectKey) -> Path:
        return self._root / key.scope.value / str(key.owner_id) / str(key.object_id)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _ensure_directory(cls, directory: Path) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for created in reversed(missing):
            cls._sync_directory(created.parent)


class S3ObjectStore:
    """AWS S3-compatible adapter used with AWS or RustFS without provider APIs."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: ObjectKey, content: bytes) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key.as_posix(),
                Body=content,
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStoreError("Object storage operation failed") from error

    def get(self, key: ObjectKey) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key.as_posix())
            return response["Body"].read()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                raise ObjectNotFoundError("Object does not exist") from None
            raise ObjectStoreError("Object storage operation failed") from error
        except BotoCoreError as error:
            raise ObjectStoreError("Object storage operation failed") from error

    def delete(self, key: ObjectKey) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key.as_posix())
        except (BotoCoreError, ClientError) as error:
            raise ObjectStoreError("Object storage operation failed") from error

    def exists(self, key: ObjectKey) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key.as_posix())
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return False
            raise ObjectStoreError("Object storage operation failed") from error
        except BotoCoreError as error:
            raise ObjectStoreError("Object storage operation failed") from error
        return True

    def is_ready(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except BotoCoreError, ClientError:
            return False
        return True
