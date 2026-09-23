"""Authenticated encryption for operator-keyed Composer connection secrets."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_FORMAT = b"MWC1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_HEX_KEY_BYTES = 64


class SecretError(Exception):
    """A connection secret cannot be encrypted or opened safely."""


@dataclass(frozen=True, slots=True)
class PlainCredentials:
    """Write-only credential input; never serialize this value in a read response."""

    api_key: bytes | None = field(default=None, repr=False)
    client_certificate: bytes | None = field(default=None, repr=False)
    client_private_key: bytes | None = field(default=None, repr=False)
    ca_bundle: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.client_certificate is None) != (self.client_private_key is None):
            raise ValueError(
                "Client certificate and private key must be supplied together"
            )
        for value in (
            self.api_key,
            self.client_certificate,
            self.client_private_key,
            self.ca_bundle,
        ):
            if value is not None and not value:
                raise ValueError("Credential values cannot be empty")


@dataclass(frozen=True, slots=True)
class EncryptedCredentials:
    """Opaque encrypted fields stored by connection repositories."""

    api_key: bytes | None = field(default=None, repr=False)
    client_certificate: bytes | None = field(default=None, repr=False)
    client_private_key: bytes | None = field(default=None, repr=False)
    ca_bundle: bytes | None = field(default=None, repr=False)


class SecretCipher:
    """AES-GCM envelope using a key supplied outside the database and image."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError("Composer secret key must be exactly 32 bytes")
        self._aead = AESGCM(key)
        self._fingerprint = hashlib.sha256(
            b"markweave-composer-key-v1\0" + key
        ).hexdigest()

    @property
    def fingerprint(self) -> str:
        """Return a nonsecret key identity for restore validation."""

        return self._fingerprint

    @classmethod
    def from_key_file(cls, path: str | Path) -> SecretCipher:
        """Load 32 key bytes as 64 hex characters from a 0400/0600/0440/0640 file."""

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) & ~0o640
                ):
                    raise SecretError("Composer secret key file is not private")
                raw = os.read(descriptor, 66)
            finally:
                os.close(descriptor)
        except OSError:
            raise SecretError("Composer secret key file is unavailable") from None
        encoded = raw.removesuffix(b"\n")
        if len(encoded) != _HEX_KEY_BYTES:
            raise SecretError("Composer secret key file is invalid")
        try:
            return cls(bytes.fromhex(encoded.decode("ascii")))
        except UnicodeDecodeError, ValueError:
            raise SecretError("Composer secret key file is invalid") from None

    def seal(self, connection_id: UUID, field: str, plaintext: bytes) -> bytes:
        if not plaintext:
            raise SecretError("Credential value is empty")
        nonce = os.urandom(_NONCE_BYTES)
        return (
            _FORMAT
            + nonce
            + self._aead.encrypt(
                nonce, plaintext, _associated_data(connection_id, field)
            )
        )

    def open(self, connection_id: UUID, field: str, ciphertext: bytes) -> bytes:
        if len(ciphertext) < len(
            _FORMAT
        ) + _NONCE_BYTES + 16 or not ciphertext.startswith(_FORMAT):
            raise SecretError("Connection credential is unavailable")
        nonce_start = len(_FORMAT)
        nonce_end = nonce_start + _NONCE_BYTES
        try:
            return self._aead.decrypt(
                ciphertext[nonce_start:nonce_end],
                ciphertext[nonce_end:],
                _associated_data(connection_id, field),
            )
        except InvalidTag:
            raise SecretError("Connection credential is unavailable") from None

    def seal_credentials(
        self, connection_id: UUID, credentials: PlainCredentials
    ) -> EncryptedCredentials:
        return EncryptedCredentials(
            **{
                field: self.seal(connection_id, field, value)
                if value is not None
                else None
                for field, value in (
                    ("api_key", credentials.api_key),
                    ("client_certificate", credentials.client_certificate),
                    ("client_private_key", credentials.client_private_key),
                    ("ca_bundle", credentials.ca_bundle),
                )
            }
        )

    def open_credentials(
        self, connection_id: UUID, credentials: EncryptedCredentials
    ) -> PlainCredentials:
        try:
            return PlainCredentials(
                **{
                    field: self.open(connection_id, field, value)
                    if value is not None
                    else None
                    for field, value in (
                        ("api_key", credentials.api_key),
                        ("client_certificate", credentials.client_certificate),
                        ("client_private_key", credentials.client_private_key),
                        ("ca_bundle", credentials.ca_bundle),
                    )
                }
            )
        except ValueError:
            raise SecretError("Connection credential is unavailable") from None


def _associated_data(connection_id: UUID, field: str) -> bytes:
    if field not in {
        "api_key",
        "client_certificate",
        "client_private_key",
        "ca_bundle",
    }:
        raise SecretError("Credential field is invalid")
    return b"markweave-composer\0" + connection_id.bytes + b"\0" + field.encode("ascii")
