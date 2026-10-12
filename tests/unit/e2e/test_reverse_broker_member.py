"""Bound the test-only unit-slice member observer and its failure cleanup."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.broker.models import ManagedUnitState
from scripts.e2e import reverse_broker as fixture

pytestmark = pytest.mark.unit

METRICS_URL = "http://127.0.0.1:9464/metrics"
CREATED = ManagedUnitState.CREATED
EXIT = ManagedUnitState.EXIT_CONFIRMED
REMOVED = ManagedUnitState.REMOVED


def _metrics(
    ready: float = 1, fault: float = 0, unproven: float = 0, transport: float = 0
) -> dict[str, float | None]:
    return {
        "broker_ready": ready,
        "broker_fault": fault,
        "faults_termination_unproven": unproven,
        "faults_transport_failure": transport,
    }


class _Sticky:
    """Return scripted values in order, then repeat the last one."""

    def __init__(self, values: Iterable[Any]) -> None:
        self._values = list(values)

    def __call__(self, *_args: object, **_kwargs: object) -> Any:
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


def _setup(
    tmp_path: Path,
    mocker: MockerFixture,
    *,
    states: Iterable[ManagedUnitState | None],
    metrics: Iterable[dict[str, float | None]],
    new_units: int = 1,
) -> SimpleNamespace:
    unit_ids = [uuid4() for _ in range(new_units)]
    units = [
        SimpleNamespace(
            unit_id=unit_id,
            principal=SimpleNamespace(principal_id=fixture.WORKER_ID),
            policy_revision=fixture.POLICY,
        )
        for unit_id in unit_ids
    ]
    config = SimpleNamespace(
        state_directory=tmp_path, authentication_key=b"x" * 32, max_units=16
    )
    mocker.patch.object(fixture, "load_broker_process_config", return_value=config)
    inventory = mocker.Mock()
    inventory.unacknowledged.side_effect = _Sticky([(), tuple(units)])
    state_script = _Sticky(states)

    def get(_unit_id: object) -> SimpleNamespace | None:
        state = state_script()
        return None if state is None else SimpleNamespace(state=state)

    inventory.get.side_effect = get
    inventory.get_proof.return_value = None
    mocker.patch.object(fixture, "SQLiteBrokerInventory", return_value=inventory)
    mocker.patch.object(fixture, "_unit_slice_exists", return_value=True)
    mocker.patch.object(fixture, "_member_metrics", side_effect=_Sticky(metrics))
    start = mocker.patch.object(fixture, "_start_slice_member")
    members: dict[str, bool] = {}

    def systemctl(*arguments: str, check: bool = True) -> str:
        del check
        if arguments[0] == "stop":
            members[arguments[1]] = False
        return "active" if members.get(arguments[-1], True) else "inactive"

    systemctl_mock = mocker.patch.object(
        fixture, "_systemctl_user", side_effect=systemctl
    )
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds

    mocker.patch.object(
        fixture,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            monotonic_ns=lambda: int(clock.now * 1_000_000_000),
            sleep=sleep,
        ),
    )
    return SimpleNamespace(
        clock=clock,
        unit_ids=unit_ids,
        inventory=inventory,
        start=start,
        systemctl=systemctl_mock,
        members=members,
        ready=tmp_path / "member.ready",
        report=tmp_path / "member-report.json",
    )


def _member(unit_id: UUID) -> str:
    return f"{fixture.MEMBER_UNIT_PREFIX}{unit_id.hex}.service"


def _stops(setup: SimpleNamespace) -> list[tuple[object, ...]]:
    return [
        call.args for call in setup.systemctl.call_args_list if call.args[0] == "stop"
    ]


def test_persistent_member_holds_until_the_worker_reports_the_broker_fault(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path,
        mocker,
        states=[CREATED, CREATED, CREATED, EXIT, EXIT, EXIT, REMOVED],
        metrics=[
            _metrics(),
            _metrics(ready=1),
            _metrics(ready=0, fault=1, transport=1),
            _metrics(ready=1),
        ],
    )

    fixture.watch_member(tmp_path, "persistent", METRICS_URL, setup.ready, setup.report)

    unit_id = setup.unit_ids[0]
    member = _member(unit_id)
    setup.start.assert_called_once_with(f"markweavet70{unit_id.hex}.slice", member)
    assert _stops(setup) == [("stop", member)]
    report = json.loads(setup.report.read_text())
    assert report["schema"] == "t137-reverse-member-v1"
    assert report["phase"] == "proven"
    assert report["unit_id"] == str(unit_id)
    assert report["armed_state"] == "created"
    assert report["held_state"] == "exit_confirmed"
    assert report["held_metrics"]["broker_fault"] == 1
    assert report["final_state"] == "removed"
    assert report["final_metrics"]["broker_ready"] == 1
    assert report["released_at"] is not None


def test_transient_member_releases_after_a_short_hold(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path,
        mocker,
        states=[CREATED, CREATED, EXIT, EXIT, None],
        metrics=[_metrics()],
    )

    fixture.watch_member(tmp_path, "transient", METRICS_URL, setup.ready, setup.report)

    assert fixture._MEMBER_TRANSIENT_HOLD_SECONDS in setup.clock.sleeps
    report = json.loads(setup.report.read_text())
    assert report["phase"] == "proven"
    assert report["held_metrics"]["broker_ready"] == 1
    assert report["final_state"] == "absent"


def test_member_armed_after_unit_exit_fails_and_stops_the_member(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(tmp_path, mocker, states=[EXIT], metrics=[_metrics()])

    with pytest.raises(RuntimeError, match="not armed before unit exit"):
        fixture.watch_member(
            tmp_path, "persistent", METRICS_URL, setup.ready, setup.report
        )

    assert _stops(setup) == [("stop", _member(setup.unit_ids[0]))]
    report = json.loads(setup.report.read_text())
    assert report["phase"] == "failed"
    assert report["failure"] == "RuntimeError"


def test_emptiness_proven_while_the_member_lives_fails(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path, mocker, states=[CREATED, CREATED, REMOVED], metrics=[_metrics()]
    )

    with pytest.raises(RuntimeError, match="proved emptiness with a live member"):
        fixture.watch_member(
            tmp_path, "transient", METRICS_URL, setup.ready, setup.report
        )

    assert _stops(setup) == [("stop", _member(setup.unit_ids[0]))]


def test_proof_observed_during_the_hold_fails(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path, mocker, states=[CREATED, CREATED, EXIT], metrics=[_metrics()]
    )
    setup.inventory.get_proof.return_value = object()

    with pytest.raises(RuntimeError, match="proved emptiness with a live member"):
        fixture.watch_member(
            tmp_path, "transient", METRICS_URL, setup.ready, setup.report
        )

    assert _stops(setup) == [("stop", _member(setup.unit_ids[0]))]


def test_more_than_one_new_unit_is_refused_before_any_member_starts(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path, mocker, states=[CREATED], metrics=[_metrics()], new_units=2
    )

    with pytest.raises(RuntimeError, match="not unique"):
        fixture.watch_member(
            tmp_path, "persistent", METRICS_URL, setup.ready, setup.report
        )

    setup.start.assert_not_called()
    assert _stops(setup) == []


def test_persistent_member_requires_a_new_broker_fault(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    setup = _setup(
        tmp_path,
        mocker,
        states=[CREATED, CREATED, EXIT],
        metrics=[_metrics(transport=3), _metrics(ready=0, fault=1, transport=3)],
    )
    with pytest.raises(RuntimeError, match="did not report the broker fault"):
        fixture.watch_member(
            tmp_path, "persistent", METRICS_URL, setup.ready, setup.report
        )

    assert _stops(setup) == [("stop", _member(setup.unit_ids[0]))]


def test_invalid_member_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode is invalid"):
        fixture.watch_member(
            tmp_path, "forever", METRICS_URL, tmp_path / "r", tmp_path / "p"
        )


def test_metrics_projection_requires_loopback_and_runtime_gauges(
    mocker: MockerFixture,
) -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        fixture._read_metrics("http://example.test/metrics")
    reader = mocker.patch.object(
        fixture,
        "_read_metrics",
        return_value={
            "md_converter_reversion_broker_ready": 0.0,
            "md_converter_reversion_broker_fault": 1.0,
            'md_converter_reversion_runtime_faults_total{code="transport_failure"}': 2.0,
        },
    )
    assert fixture._member_metrics(METRICS_URL) == {
        "broker_ready": 0.0,
        "broker_fault": 1.0,
        "faults_termination_unproven": 0.0,
        "faults_transport_failure": 2.0,
    }
    reader.return_value = {}
    with pytest.raises(RuntimeError, match="gauges are unavailable"):
        fixture._member_metrics(METRICS_URL)


def test_metrics_reader_parses_exposition_lines(mocker: MockerFixture) -> None:
    body = (
        b"# HELP ignored\n"
        b"md_converter_reversion_broker_ready 1\n"
        b'md_converter_reversion_runtime_faults_total{code="termination_unproven"} 2\n'
        b"\n"
    )
    response = mocker.MagicMock()
    response.__enter__.return_value.read.return_value = body
    mocker.patch.object(fixture.urllib.request, "urlopen", return_value=response)

    assert fixture._read_metrics(METRICS_URL) == {
        "md_converter_reversion_broker_ready": 1.0,
        'md_converter_reversion_runtime_faults_total{code="termination_unproven"}': 2.0,
    }
