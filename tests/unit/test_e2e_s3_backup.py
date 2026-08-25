"""Deterministic contracts for the T21 RustFS snapshot helper."""

from __future__ import annotations

import argparse
import hashlib
import json
from io import BytesIO
from typing import Any

import pytest

from scripts.e2e import s3_backup


class _Paginator:
    def __init__(self, client: _FakeS3) -> None:
        self._client = client

    def paginate(self, *, Bucket: str) -> list[dict[str, object]]:
        assert Bucket == "bucket"
        return [{"Contents": [{"Key": key} for key in self._client.objects]}]


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.deleted: list[str] = []

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return _Paginator(self)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        assert Bucket == "bucket"
        return {"Body": BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        assert Bucket == "bucket"
        self.deleted.append(Key)
        self.objects.pop(Key)

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        assert Bucket == "bucket"
        self.objects[Key] = Body


def _arguments(tmp_path: Any, operation: str) -> argparse.Namespace:
    return argparse.Namespace(
        operation=operation,
        endpoint_url="http://rustfs.invalid",
        region="us-east-1",
        access_key_id="access",
        secret_access_key="t21-" + "secret",
        bucket="bucket",
        directory=tmp_path / "snapshot",
    )


@pytest.mark.unit
def test_backup_and_restore_replace_bucket_with_verified_snapshot(
    tmp_path: Any, mocker: Any
) -> None:
    original = {"one": b"first", "nested/two": b"second"}
    client = _FakeS3(original)
    mocker.patch.object(s3_backup, "_client", return_value=client)

    arguments = _arguments(tmp_path, "backup")
    s3_backup.backup(arguments)
    manifest = json.loads(
        (arguments.directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert [item["key"] for item in manifest] == ["nested/two", "one"]
    assert manifest[0]["sha256"] == hashlib.sha256(b"second").hexdigest()

    client.objects = {"unrelated": b"remove-me"}
    s3_backup.restore(arguments)
    assert client.deleted == ["unrelated"]
    assert client.objects == original


@pytest.mark.unit
def test_restore_rejects_tampered_snapshot(tmp_path: Any, mocker: Any) -> None:
    client = _FakeS3({"one": b"first"})
    mocker.patch.object(s3_backup, "_client", return_value=client)
    arguments = _arguments(tmp_path, "backup")
    s3_backup.backup(arguments)
    digest = hashlib.sha256(b"first").hexdigest()
    (arguments.directory / digest).write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="size mismatch"):
        s3_backup.restore(arguments)
    assert client.deleted == []
    assert client.objects == {"one": b"first"}
