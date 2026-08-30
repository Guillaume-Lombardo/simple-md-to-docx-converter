"""Test-only setup and verification for final-image recovery CLI smoke coverage."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

import boto3
from sqlalchemy import insert, select

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine, standalone_database_url


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["RECOVERY_S3_ENDPOINT"],
        region_name="us-east-1",
        aws_access_key_id=os.environ["RECOVERY_S3_ACCESS"],
        aws_secret_access_key=os.environ["RECOVERY_S3_SECRET"],
    )


def _upgrade_and_seed(database_url) -> None:
    engine = create_database_engine(database_url)
    try:
        upgrade_database(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(UserRow),
                {
                    "id": str(uuid4()),
                    "username": "Recovery E2E",
                    "normalized_username": f"recovery-e2e-{uuid4().hex}",
                    "password_hash": "hash:e2e",
                    "role": "user",
                    "active": True,
                    "auth_version": 0,
                    "password_change_required": False,
                },
            )
    finally:
        engine.dispose()


def standalone_initialize(path: Path) -> None:
    path.mkdir(mode=0o700)
    object_path = path / "objects" / "uploads" / str(uuid4()) / str(uuid4())
    object_path.parent.mkdir(mode=0o700, parents=True)
    object_path.write_bytes(b"final-image-standalone")
    _upgrade_and_seed(standalone_database_url(path))


def standalone_verify(path: Path) -> None:
    engine = create_database_engine(standalone_database_url(path))
    try:
        with engine.connect() as connection:
            if connection.scalar(select(UserRow.username)) != "Recovery E2E":
                raise RuntimeError("standalone restored database is invalid")
    finally:
        engine.dispose()
    objects = [
        item.read_bytes() for item in (path / "objects").rglob("*") if item.is_file()
    ]
    if objects != [b"final-image-standalone"]:
        raise RuntimeError("standalone restored objects are invalid")


def distributed_initialize() -> None:
    _upgrade_and_seed(os.environ["RECOVERY_DATABASE"])
    client = _s3()
    for bucket in (
        os.environ["RECOVERY_SOURCE_BUCKET"],
        os.environ["RECOVERY_TARGET_BUCKET"],
        os.environ["RECOVERY_FAILED_BUCKET"],
    ):
        client.create_bucket(Bucket=bucket)
    client.put_object(
        Bucket=os.environ["RECOVERY_SOURCE_BUCKET"],
        Key=f"uploads/{uuid4()}/{uuid4()}",
        Body=b"final-image-distributed",
    )


def distributed_verify() -> None:
    engine = create_database_engine(os.environ["RECOVERY_DATABASE"])
    try:
        with engine.connect() as connection:
            if connection.scalar(select(UserRow.username)) != "Recovery E2E":
                raise RuntimeError("distributed restored database is invalid")
    finally:
        engine.dispose()
    client = _s3()
    target = client.list_objects_v2(Bucket=os.environ["RECOVERY_TARGET_BUCKET"])
    failed = client.list_objects_v2(Bucket=os.environ["RECOVERY_FAILED_BUCKET"])
    if target.get("KeyCount") != 1 or failed.get("KeyCount") != 0:
        raise RuntimeError("distributed restore cleanup is invalid")


def tamper(path: Path) -> None:
    database = path / "database" / "metadata.sqlite3"
    database.write_bytes(database.read_bytes() + b"tampered")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=(
            "standalone-initialize",
            "standalone-verify",
            "distributed-initialize",
            "distributed-verify",
            "tamper",
        ),
    )
    parser.add_argument("--path", required=False, type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "standalone-initialize":
        standalone_initialize(arguments.path)
    elif arguments.operation == "standalone-verify":
        standalone_verify(arguments.path)
    elif arguments.operation == "distributed-initialize":
        distributed_initialize()
    elif arguments.operation == "distributed-verify":
        distributed_verify()
    else:
        tamper(arguments.path)


if __name__ == "__main__":
    main()
