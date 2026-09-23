"""Bounded model-step execution with durable cancellation and proposal gates."""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import Any
from uuid import UUID

from markweave.composer.connections import ConnectionActor, ConnectionService
from markweave.composer.egress import (
    EgressCancelledError,
    EgressCapacityError,
    EgressResponseError,
    EgressUnavailableError,
)
from markweave.http.composer_errors import ComposerUnavailableError
from markweave.observability import OperationalMetrics
from markweave.persistence.composer import (
    SYSTEM_ACTOR_ID,
    ComposerModelStep,
    SqlComposerModelStepRepository,
)

_CANCELLATION_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class ModelStepInput:
    connection_id: UUID
    if_match: str
    idempotency_key: str
    approved_endpoint: str
    approved_model: str
    content: str
    max_output_tokens: int
    payload_digest: str


class ComposerStepRunner:
    """Run explicit owner-approved model text without publishing cancelled output."""

    def __init__(
        self,
        repository: SqlComposerModelStepRepository,
        connections: ConnectionService,
        *,
        maximum_active: int,
        lease: timedelta,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._repository = repository
        self._connections = connections
        self._maximum_active = maximum_active
        self._lease = lease
        self._metrics = metrics
        self._lock = threading.Lock()
        self._active: dict[
            UUID, tuple[ComposerModelStep, threading.Event, threading.Thread]
        ] = {}
        self._closed = False

    def start(
        self,
        actor: ConnectionActor,
        draft_id: UUID,
        input_: ModelStepInput,
    ) -> ComposerModelStep:
        """Durably admit one call before starting a bounded background thread."""

        with self._lock:
            if self._closed:
                raise ComposerUnavailableError
        step, created = self._repository.start_model_step(
            actor.id,
            draft_id,
            connection_id=input_.connection_id,
            if_match=input_.if_match,
            idempotency_key=input_.idempotency_key,
            approved_endpoint=input_.approved_endpoint,
            approved_model=input_.approved_model,
            payload_digest=input_.payload_digest,
            max_active=self._maximum_active,
            lease=self._lease,
        )
        if not created:
            if self._metrics is not None:
                self._metrics.record_composer_model_step_retry()
            return step
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(actor, step, input_.content, input_.max_output_tokens, cancel_event),
            daemon=True,
            name="composer-model-step",
        )
        launch_failed = False
        with self._lock:
            if self._closed:
                self._repository.cancel_model_step(
                    actor.id, draft_id, step.id, actor_id=SYSTEM_ACTOR_ID
                )
                raise ComposerUnavailableError
            self._active[step.id] = (step, cancel_event, thread)
            self._record_active_count()
            try:
                thread.start()
            except RuntimeError:
                self._active.pop(step.id, None)
                self._record_active_count()
                launch_failed = True
        if launch_failed:
            failed = self._repository.fail_model_step(
                actor.id, draft_id, step.id, error_code="execution_unavailable"
            )
            if (
                self._metrics is not None
                and failed.status == "failed"
                and failed.error_code == "execution_unavailable"
            ):
                self._metrics.record_composer_model_step_failure(
                    "execution_unavailable"
                )
            raise ComposerUnavailableError from None
        return step

    def get(self, owner_id: UUID, draft_id: UUID, step_id: UUID) -> ComposerModelStep:
        """Read the owner-scoped durable state from any application replica."""

        return self._repository.get_model_step(owner_id, draft_id, step_id)

    def cancel(
        self, owner_id: UUID, draft_id: UUID, step_id: UUID
    ) -> ComposerModelStep:
        """Commit cancellation before signalling any locally running transport."""

        step = self._repository.cancel_model_step(owner_id, draft_id, step_id)
        with self._lock:
            local = self._active.get(step_id)
        if local is not None:
            local[1].set()
        return step

    def close(self) -> None:
        """Stop local transports; durable state remains fenced across restart."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = tuple(self._active.items())
        for step_id, (step, cancel_event, _thread) in active:
            cancel_event.set()
            # An unavailable store leaves a fenced row for lease recovery.
            with suppress(Exception):
                self._repository.cancel_model_step(
                    step.owner_id,
                    step.draft_id,
                    step_id,
                    actor_id=SYSTEM_ACTOR_ID,
                )
        deadline = monotonic() + min(self._lease.total_seconds(), 2.0)
        for _step_id, (_step, _event, thread) in active:
            thread.join(timeout=max(0.0, deadline - monotonic()))

    def _run(
        self,
        actor: ConnectionActor,
        step: ComposerModelStep,
        content: str,
        max_output_tokens: int,
        cancel_event: threading.Event,
    ) -> None:
        started_at = self._metrics.timer() if self._metrics is not None else 0.0
        outcome = "failed"
        watcher_done = threading.Event()
        watcher = threading.Thread(
            target=self._watch_cancellation,
            args=(step, cancel_event, watcher_done),
            daemon=True,
            name="composer-cancel-watch",
        )
        watcher_started = False
        try:
            watcher.start()
            watcher_started = True
            current = self._repository.get_model_step(actor.id, step.draft_id, step.id)
            if current.status != "running":
                outcome = current.status
                cancel_event.set()
                return
            response = self._connections.chat(
                actor,
                step.connection_id,
                [{"role": "user", "content": content}],
                max_output_tokens=max_output_tokens,
                cancel_event=cancel_event,
            )
            proposed_value = _completion_content(response)
            if cancel_event.is_set():
                outcome = "cancelled"
                return
            self._repository.finish_model_step(
                actor.id,
                step.draft_id,
                step.id,
                proposed_value=proposed_value,
                provenance=f"model-step:{step.id}",
            )
            outcome = "completed"
        except EgressCancelledError:
            outcome = "cancelled"
            with suppress(Exception):
                self._repository.cancel_model_step(
                    actor.id,
                    step.draft_id,
                    step.id,
                    actor_id=SYSTEM_ACTOR_ID,
                )
        except EgressCapacityError:
            self._fail(step, "capacity_exhausted")
        except EgressUnavailableError:
            self._fail(step, "provider_unavailable")
        except EgressResponseError:
            self._fail(step, "provider_invalid")
        except Exception:
            self._fail(step, "execution_failed")
        finally:
            watcher_done.set()
            if watcher_started:
                watcher.join(timeout=_CANCELLATION_POLL_SECONDS * 2)
            with suppress(Exception):
                final = self._repository.get_model_step(
                    actor.id, step.draft_id, step.id
                )
                if final.status in {"completed", "cancelled", "failed"}:
                    outcome = final.status
            with self._lock:
                self._active.pop(step.id, None)
                self._record_active_count()
            if self._metrics is not None:
                self._metrics.record_composer_model_step_duration(
                    outcome, max(0.0, self._metrics.timer() - started_at)
                )

    def _watch_cancellation(
        self,
        step: ComposerModelStep,
        cancel_event: threading.Event,
        done: threading.Event,
    ) -> None:
        while not done.wait(_CANCELLATION_POLL_SECONDS):
            try:
                current = self._repository.get_model_step(
                    step.owner_id, step.draft_id, step.id
                )
            except Exception:
                cancel_event.set()
                return
            if current.status != "running":
                cancel_event.set()
                return

    def _fail(self, step: ComposerModelStep, code: str) -> None:
        # A transient store outage cannot publish a result; lease recovery
        # terminalizes the operation when the shared store returns.
        with suppress(Exception):
            failed = self._repository.fail_model_step(
                step.owner_id, step.draft_id, step.id, error_code=code
            )
            if (
                self._metrics is not None
                and failed.status == "failed"
                and failed.error_code == code
            ):
                self._metrics.record_composer_model_step_failure(code)

    def _record_active_count(self) -> None:
        if self._metrics is not None:
            self._metrics.set_composer_active_model_steps(len(self._active))


def _completion_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except KeyError, IndexError, TypeError:
        raise EgressResponseError("Provider completion is invalid") from None
    if not isinstance(content, str) or not content.strip():
        raise EgressResponseError("Provider completion is invalid")
    return content
