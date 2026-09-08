from __future__ import annotations

from itertools import pairwise
from typing import Any, cast
from uuid import UUID

import pytest

import markweave.broker.models as broker_models
from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    TerminationProof,
    is_next_unit_state,
    policy_specification_evidence,
)

pytestmark = pytest.mark.unit

ATTEMPT_ID = UUID("10000000-0000-4000-8000-000000000001")
UNIT_ID = UUID("20000000-0000-4000-8000-000000000002")
PRINCIPAL_ID = UUID("30000000-0000-4000-8000-000000000003")
PROOF_ID = UUID("40000000-0000-4000-8000-000000000004")
INCARNATION_ID = UUID("50000000-0000-4000-8000-000000000005")
DIGEST = "sha256:" + "a" * 64


def limits() -> RuntimeLimits:
    return RuntimeLimits(100_000, 100_000, 512_000_000, 64, 32_000_000, 30_000)


def channel_limits() -> RuntimeChannelLimits:
    return RuntimeChannelLimits(1_000_000, 2_000_000)


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(PRINCIPAL_ID)


def incarnation() -> RuntimeIncarnation:
    return RuntimeIncarnation(INCARNATION_ID, EvidenceDigest(DIGEST))


def test_runtime_policy_requires_explicit_positive_limits_and_immutable_digest() -> (
    None
):
    policy = BrokerPolicy("t71-v1", DIGEST, limits(), channel_limits())

    assert policy.image_digest == DIGEST
    assert policy.limits.pid_limit == 64
    assert policy.channel_limits.max_input_bytes == 1_000_000
    with pytest.raises(TypeError):
        cast(Any, RuntimeLimits)()


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_runtime_channel_limits_require_explicit_positive_integers(
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="channel limits"):
        RuntimeChannelLimits(cast(Any, invalid), 1)
    with pytest.raises(ValueError, match="channel limits"):
        RuntimeChannelLimits(1, cast(Any, invalid))


@pytest.mark.parametrize("field", RuntimeLimits.__dataclass_fields__)
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_runtime_limits_reject_non_positive_or_non_integer_values(
    field: str, invalid: object
) -> None:
    values = {
        "cpu_quota_micros": 1,
        "cpu_period_micros": 1,
        "memory_bytes": 1,
        "pid_limit": 1,
        "workspace_bytes": 1,
        "wall_time_millis": 1,
    }
    values[field] = invalid

    with pytest.raises(ValueError, match="positive integers"):
        RuntimeLimits(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "revision", ["", "UPPER", "-leading", "trailing-", "a" * 65, 1, True]
)
def test_policy_rejects_invalid_revision(revision: object) -> None:
    with pytest.raises(ValueError, match="revision"):
        BrokerPolicy(cast(Any, revision), DIGEST, limits(), channel_limits())


@pytest.mark.parametrize(
    "digest",
    ["a" * 64, "sha256:" + "A" * 64, "sha512:" + "a" * 64, DIGEST + "0", 1],
)
def test_policy_rejects_mutable_or_noncanonical_image_digest(digest: object) -> None:
    with pytest.raises(ValueError, match="immutable SHA-256"):
        BrokerPolicy("t71-v1", cast(Any, digest), limits(), channel_limits())


def test_policy_rejects_a_limit_lookalike() -> None:
    with pytest.raises(ValueError, match="runtime limits"):
        BrokerPolicy("t71-v1", DIGEST, cast(Any, object()), channel_limits())


def test_policy_specification_evidence_binds_fixed_runtime_contract() -> None:
    policy = BrokerPolicy("t71-v1", DIGEST, limits(), channel_limits())
    assert policy_specification_evidence(policy) == EvidenceDigest(
        "sha256:df16badaa0a81484f3d7c2a8553907ecae03436fac6a14aaca1c13dfb1c0d89f"
    )
    assert policy_specification_evidence(
        BrokerPolicy("t71-v2", DIGEST, limits(), channel_limits())
    ) != policy_specification_evidence(policy)
    assert policy_specification_evidence(
        BrokerPolicy("t71-v1", DIGEST, limits(), RuntimeChannelLimits(2, 3))
    ) != policy_specification_evidence(policy)
    with pytest.raises(ValueError, match="policy"):
        policy_specification_evidence(cast(Any, object()))


def test_policy_specification_evidence_has_no_mutable_module_security_mapping() -> None:
    policy = BrokerPolicy("t71-v1", DIGEST, limits(), channel_limits())
    expected = EvidenceDigest(
        "sha256:df16badaa0a81484f3d7c2a8553907ecae03436fac6a14aaca1c13dfb1c0d89f"
    )

    assert not hasattr(broker_models, "_FIXED_RUNTIME_SECURITY")
    assert policy_specification_evidence(policy) == expected
    assert policy_specification_evidence(policy) == expected


def test_state_machine_has_one_exact_monotonic_path() -> None:
    path = list(ManagedUnitState)
    assert path == [
        ManagedUnitState.RESERVED,
        ManagedUnitState.CREATE_INTENT,
        ManagedUnitState.CREATED,
        ManagedUnitState.EXIT_CONFIRMED,
        ManagedUnitState.EMPTY_CONFIRMED,
        ManagedUnitState.REMOVED,
    ]
    for current, target in pairwise(path):
        assert is_next_unit_state(current, target)
    for current in path:
        for target in path:
            if path.index(target) != path.index(current) + 1:
                assert not is_next_unit_state(current, target)


@pytest.mark.parametrize("sequence", [0, -1, MAX_SEQUENCE + 1, True, 1.0, "1"])
def test_replay_position_rejects_invalid_sequences(sequence: object) -> None:
    with pytest.raises(ValueError, match="sequence"):
        ReplayPosition(principal(), cast(Any, sequence))


def test_replay_position_binds_sequence_to_authenticated_principal() -> None:
    assert (
        ReplayPosition(principal(), MAX_SEQUENCE).principal.principal_id == PRINCIPAL_ID
    )


def test_replay_position_rejects_principal_lookalike() -> None:
    with pytest.raises(ValueError, match="Replay principal"):
        ReplayPosition(cast(Any, PRINCIPAL_ID), 1)


@pytest.mark.parametrize("value", [None, "id", 1, True])
def test_stable_identity_models_reject_non_uuids(value: object) -> None:
    with pytest.raises(ValueError, match="UUID"):
        AuthenticatedPrincipal(cast(Any, value))
    with pytest.raises(ValueError, match="UUID"):
        RuntimeIncarnation(cast(Any, value), EvidenceDigest(DIGEST))


def test_runtime_incarnation_rejects_raw_specification_digest() -> None:
    with pytest.raises(ValueError, match="specification evidence"):
        RuntimeIncarnation(INCARNATION_ID, cast(Any, DIGEST))


@pytest.mark.parametrize("state", list(ManagedUnitState))
def test_managed_unit_requires_incarnation_exactly_after_runtime_create(
    state: ManagedUnitState,
) -> None:
    expected = state not in {
        ManagedUnitState.RESERVED,
        ManagedUnitState.CREATE_INTENT,
    }
    value = incarnation() if expected else None
    evidence_count = min(3, max(0, list(ManagedUnitState).index(state) - 2))
    evidence = [EvidenceDigest(DIGEST)] * evidence_count + [None] * (3 - evidence_count)
    unit = ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        principal(),
        1,
        "t71-v1",
        EvidenceDigest(DIGEST),
        state,
        0,
        value,
        *evidence,
    )
    assert (unit.runtime_incarnation is not None) is expected

    with pytest.raises(ValueError, match="incarnation"):
        ManagedUnit(
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            1,
            "t71-v1",
            EvidenceDigest(DIGEST),
            state,
            0,
            None if expected else incarnation(),
            *evidence,
        )


def test_managed_unit_binds_runtime_incarnation_to_reserved_policy_specification() -> (
    None
):
    with pytest.raises(ValueError, match="runtime specification evidence"):
        ManagedUnit(
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            1,
            "t71-v1",
            EvidenceDigest("sha256:" + "b" * 64),
            ManagedUnitState.CREATED,
            0,
            incarnation(),
        )


@pytest.mark.parametrize("state", list(ManagedUnitState))
def test_managed_unit_requires_exact_state_evidence_prefix(
    state: ManagedUnitState,
) -> None:
    value = (
        None
        if state in {ManagedUnitState.RESERVED, ManagedUnitState.CREATE_INTENT}
        else incarnation()
    )
    required = min(3, max(0, list(ManagedUnitState).index(state) - 2))
    evidence = [EvidenceDigest(DIGEST)] * required + [None] * (3 - required)
    ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        principal(),
        1,
        "t71-v1",
        EvidenceDigest(DIGEST),
        state,
        0,
        value,
        *evidence,
    )
    if required:
        evidence[required - 1] = None
    else:
        evidence[0] = EvidenceDigest(DIGEST)
    with pytest.raises(ValueError, match="evidence"):
        ManagedUnit(
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            1,
            "t71-v1",
            EvidenceDigest(DIGEST),
            state,
            0,
            value,
            *evidence,
        )


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5])
def test_managed_unit_rejects_invalid_create_sequence(sequence: object) -> None:
    with pytest.raises(ValueError, match="create sequence"):
        ManagedUnit(
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            cast(Any, sequence),
            "t71-v1",
            EvidenceDigest(DIGEST),
            ManagedUnitState.RESERVED,
            0,
        )


@pytest.mark.parametrize("revision", [-1, True, 1.5])
def test_managed_unit_rejects_invalid_record_revision(revision: object) -> None:
    with pytest.raises(ValueError, match="unit revision"):
        ManagedUnit(
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            1,
            "t71-v1",
            EvidenceDigest(DIGEST),
            ManagedUnitState.RESERVED,
            cast(Any, revision),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt_id", "id", "UUID"),
        ("unit_id", "id", "UUID"),
        ("principal", PRINCIPAL_ID, "principal"),
        ("policy_revision", "UPPER", "policy revision"),
        ("policy_specification", DIGEST, "policy specification evidence"),
        ("state", "reserved", "state"),
    ],
)
def test_managed_unit_rejects_identity_and_enum_lookalikes(
    field: str, value: object, message: str
) -> None:
    values: dict[str, Any] = {
        "attempt_id": ATTEMPT_ID,
        "unit_id": UNIT_ID,
        "principal": principal(),
        "create_sequence": 1,
        "policy_revision": "t71-v1",
        "policy_specification": EvidenceDigest(DIGEST),
        "state": ManagedUnitState.RESERVED,
        "revision": 0,
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        ManagedUnit(**values)


@pytest.mark.parametrize("digest", ["", "sha256:abc", "sha256:" + "A" * 64, 1])
def test_evidence_digest_is_strict_and_content_free(digest: object) -> None:
    with pytest.raises(ValueError, match="evidence digest"):
        EvidenceDigest(cast(Any, digest))


def test_termination_proof_binds_all_stable_identities_and_positive_evidence() -> None:
    evidence = EvidenceDigest(DIGEST)
    proof = TerminationProof(
        PROOF_ID,
        ATTEMPT_ID,
        UNIT_ID,
        principal(),
        "t71-v1",
        evidence,
        evidence,
        evidence,
    )

    assert proof.removal_evidence == evidence
    assert proof.__dataclass_params__.frozen
    assert not hasattr(proof, "__dict__")


def test_termination_proof_rejects_raw_or_missing_evidence() -> None:
    evidence = EvidenceDigest(DIGEST)
    with pytest.raises(ValueError, match="evidence"):
        TerminationProof(
            PROOF_ID,
            ATTEMPT_ID,
            UNIT_ID,
            principal(),
            "t71-v1",
            evidence,
            evidence,
            cast(Any, DIGEST),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("proof_id", "id", "UUID"),
        ("attempt_id", "id", "UUID"),
        ("unit_id", "id", "UUID"),
        ("principal", PRINCIPAL_ID, "principal"),
        ("policy_revision", "UPPER", "policy revision"),
    ],
)
def test_termination_proof_rejects_identity_and_policy_lookalikes(
    field: str, value: object, message: str
) -> None:
    evidence = EvidenceDigest(DIGEST)
    values: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "attempt_id": ATTEMPT_ID,
        "unit_id": UNIT_ID,
        "principal": principal(),
        "policy_revision": "t71-v1",
        "exit_evidence": evidence,
        "empty_evidence": evidence,
        "removal_evidence": evidence,
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        TerminationProof(**values)
