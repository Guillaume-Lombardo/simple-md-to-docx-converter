"""Shared lifecycle conformance assertions for every isolation runtime backend."""

from markweave.broker.models import BrokerPolicy, ManagedUnit
from markweave.broker.ports import IsolationRuntime


def assert_lifecycle_conformance(
    runtime: IsolationRuntime,
    unit: ManagedUnit,
    policy: BrokerPolicy,
) -> None:
    """Exercise the common positive-evidence lifecycle in required order."""

    runtime_unit = runtime.create(unit, policy)
    assert runtime_unit.unit_id == unit.unit_id
    assert runtime_unit.incarnation.specification == unit.policy_specification
    assert runtime.discover(limit=1) == (runtime_unit,)
    runtime.hard_terminate(runtime_unit)
    exit_evidence = runtime.confirm_exit(runtime_unit)
    empty_evidence = runtime.confirm_empty(runtime_unit)
    assert exit_evidence != empty_evidence
    runtime.remove(runtime_unit)
    removal_evidence = runtime.confirm_removed(runtime_unit)
    assert removal_evidence not in {exit_evidence, empty_evidence}
    assert runtime.discover(limit=1) == ()
