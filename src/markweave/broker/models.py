"""Content-free domain models for the reverse-isolation broker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from json import dumps
from uuid import UUID

_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POLICY_REVISION_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?\Z")
_EVIDENCE_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_SEQUENCE = (1 << 63) - 1
_FIXED_ATTEMPT_ENTRYPOINT = (
    "python",
    "-m",
    "markweave.reversions.attempt_main",
)
_FIXED_RUNTIME_CAPABILITIES: tuple[str, ...] = ()
_FIXED_RUNTIME_NETWORK = "none"
_FIXED_RUNTIME_RUN_AS = "arbitrary_non_root"
_FIXED_RUNTIME_WORKSPACE = "/work"


def _require_uuid(value: object, description: str) -> None:
    if type(value) is not UUID:
        raise ValueError(f"{description} must be a UUID")


class ManagedUnitState(StrEnum):
    """Durable monotonic states of one broker-managed isolation unit."""

    RESERVED = "reserved"
    CREATE_INTENT = "create_intent"
    CREATED = "created"
    EXIT_CONFIRMED = "exit_confirmed"
    EMPTY_CONFIRMED = "empty_confirmed"
    REMOVED = "removed"


UNIT_STATE_SUCCESSOR: dict[ManagedUnitState, ManagedUnitState] = {
    ManagedUnitState.RESERVED: ManagedUnitState.CREATE_INTENT,
    ManagedUnitState.CREATE_INTENT: ManagedUnitState.CREATED,
    ManagedUnitState.CREATED: ManagedUnitState.EXIT_CONFIRMED,
    ManagedUnitState.EXIT_CONFIRMED: ManagedUnitState.EMPTY_CONFIRMED,
    ManagedUnitState.EMPTY_CONFIRMED: ManagedUnitState.REMOVED,
}


def is_next_unit_state(current: ManagedUnitState, target: ManagedUnitState) -> bool:
    """Return whether target is the sole legal durable successor."""

    return UNIT_STATE_SUCCESSOR.get(current) is target


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Positive T71-supplied ceilings enforced by a runtime backend."""

    cpu_quota_micros: int
    cpu_period_micros: int
    memory_bytes: int
    pid_limit: int
    workspace_bytes: int
    wall_time_millis: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.cpu_quota_micros,
                self.cpu_period_micros,
                self.memory_bytes,
                self.pid_limit,
                self.workspace_bytes,
                self.wall_time_millis,
            )
        ):
            raise ValueError("Broker runtime limits must be positive integers")


@dataclass(frozen=True, slots=True)
class BrokerPolicy:
    """One fixed broker-owned image and runtime policy revision."""

    revision: str
    image_digest: str
    limits: RuntimeLimits

    def __post_init__(self) -> None:
        if (
            type(self.revision) is not str
            or _POLICY_REVISION_PATTERN.fullmatch(self.revision) is None
        ):
            raise ValueError("Broker policy revision is invalid")
        if (
            type(self.image_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.image_digest) is None
        ):
            raise ValueError("Broker attempt image digest must be immutable SHA-256")
        if type(self.limits) is not RuntimeLimits:
            raise ValueError("Broker runtime limits are invalid")


def policy_specification_evidence(policy: BrokerPolicy) -> EvidenceDigest:
    """Digest the fixed execution contract and T71-owned runtime ceilings."""

    if type(policy) is not BrokerPolicy:
        raise ValueError("Broker policy is invalid")
    payload = dumps(
        {
            "argv": [],
            "entrypoint": _FIXED_ATTEMPT_ENTRYPOINT,
            "image_digest": policy.image_digest,
            "limits": {
                field: getattr(policy.limits, field)
                for field in RuntimeLimits.__dataclass_fields__
            },
            "policy_revision": policy.revision,
            "security": {
                "capabilities": list(_FIXED_RUNTIME_CAPABILITIES),
                "network": _FIXED_RUNTIME_NETWORK,
                "no_new_privileges": True,
                "read_only_root": True,
                "run_as": _FIXED_RUNTIME_RUN_AS,
                "service_account_automount": False,
                "service_links": False,
                "workspace": _FIXED_RUNTIME_WORKSPACE,
            },
            "schema_version": 1,
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return EvidenceDigest(f"sha256:{sha256(payload).hexdigest()}")


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Opaque broker-local identity derived from the authenticated transport."""

    principal_id: UUID

    def __post_init__(self) -> None:
        _require_uuid(self.principal_id, "Authenticated principal identity")


@dataclass(frozen=True, slots=True)
class ReplayPosition:
    """A strictly positive request position scoped to one authenticated principal."""

    principal: AuthenticatedPrincipal
    sequence: int

    def __post_init__(self) -> None:
        if type(self.principal) is not AuthenticatedPrincipal:
            raise ValueError("Replay principal is invalid")
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_SEQUENCE:
            raise ValueError("Broker request sequence is invalid")


@dataclass(frozen=True, slots=True)
class EvidenceDigest:
    """Digest of bounded backend evidence; never raw runtime output."""

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or _EVIDENCE_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("Runtime evidence digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class RuntimeIncarnation:
    """Exact runtime-created incarnation and its fixed specification evidence."""

    incarnation_id: UUID
    specification: EvidenceDigest

    def __post_init__(self) -> None:
        _require_uuid(self.incarnation_id, "Runtime incarnation identity")
        if type(self.specification) is not EvidenceDigest:
            raise ValueError("Runtime specification evidence is invalid")


@dataclass(frozen=True, slots=True)
class ManagedUnit:
    """The complete content-free durable identity of one managed unit."""

    attempt_id: UUID
    unit_id: UUID
    principal: AuthenticatedPrincipal
    create_sequence: int
    policy_revision: str
    policy_specification: EvidenceDigest
    state: ManagedUnitState
    revision: int
    runtime_incarnation: RuntimeIncarnation | None = None
    exit_evidence: EvidenceDigest | None = None
    empty_evidence: EvidenceDigest | None = None
    removal_evidence: EvidenceDigest | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.attempt_id, "Reverse attempt identity")
        _require_uuid(self.unit_id, "Managed unit identity")
        if type(self.principal) is not AuthenticatedPrincipal:
            raise ValueError("Managed unit principal is invalid")
        if (
            type(self.create_sequence) is not int
            or not 1 <= self.create_sequence <= MAX_SEQUENCE
        ):
            raise ValueError("Managed unit create sequence is invalid")
        if (
            type(self.policy_revision) is not str
            or _POLICY_REVISION_PATTERN.fullmatch(self.policy_revision) is None
        ):
            raise ValueError("Managed unit policy revision is invalid")
        if type(self.policy_specification) is not EvidenceDigest:
            raise ValueError("Managed unit policy specification evidence is invalid")
        if type(self.state) is not ManagedUnitState:
            raise ValueError("Managed unit state is invalid")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Managed unit revision is invalid")
        requires_incarnation = self.state in {
            ManagedUnitState.CREATED,
            ManagedUnitState.EXIT_CONFIRMED,
            ManagedUnitState.EMPTY_CONFIRMED,
            ManagedUnitState.REMOVED,
        }
        if (
            requires_incarnation
            and type(self.runtime_incarnation) is not RuntimeIncarnation
        ) or (not requires_incarnation and self.runtime_incarnation is not None):
            raise ValueError("Managed unit runtime incarnation is inconsistent")
        if (
            self.runtime_incarnation is not None
            and self.runtime_incarnation.specification != self.policy_specification
        ):
            raise ValueError(
                "Managed unit runtime specification evidence is inconsistent"
            )
        evidence_fields = (
            self.exit_evidence,
            self.empty_evidence,
            self.removal_evidence,
        )
        required_count = {
            ManagedUnitState.RESERVED: 0,
            ManagedUnitState.CREATE_INTENT: 0,
            ManagedUnitState.CREATED: 0,
            ManagedUnitState.EXIT_CONFIRMED: 1,
            ManagedUnitState.EMPTY_CONFIRMED: 2,
            ManagedUnitState.REMOVED: 3,
        }[self.state]
        if any(
            (index < required_count and type(value) is not EvidenceDigest)
            or (index >= required_count and value is not None)
            for index, value in enumerate(evidence_fields)
        ):
            raise ValueError("Managed unit evidence is inconsistent")


@dataclass(frozen=True, slots=True)
class TerminationProof:
    """Content-free positive proof bound to one principal, attempt and unit."""

    proof_id: UUID
    attempt_id: UUID
    unit_id: UUID
    principal: AuthenticatedPrincipal
    policy_revision: str
    exit_evidence: EvidenceDigest
    empty_evidence: EvidenceDigest
    removal_evidence: EvidenceDigest

    def __post_init__(self) -> None:
        for value, description in (
            (self.proof_id, "Termination proof identity"),
            (self.attempt_id, "Reverse attempt identity"),
            (self.unit_id, "Managed unit identity"),
        ):
            _require_uuid(value, description)
        if type(self.principal) is not AuthenticatedPrincipal:
            raise ValueError("Termination proof principal is invalid")
        if (
            type(self.policy_revision) is not str
            or _POLICY_REVISION_PATTERN.fullmatch(self.policy_revision) is None
        ):
            raise ValueError("Termination proof policy revision is invalid")
        if any(
            type(value) is not EvidenceDigest
            for value in (
                self.exit_evidence,
                self.empty_evidence,
                self.removal_evidence,
            )
        ):
            raise ValueError("Termination proof evidence is invalid")
