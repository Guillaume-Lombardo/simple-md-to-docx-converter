"""Production-neutral reverse queue admission policy."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReversionAdmissionPolicy:
    """Separately injected owner limit and shared global capacity."""

    active_jobs_per_user: int
    global_queue_capacity: int

    def __post_init__(self) -> None:
        for name, value in (
            ("Reverse active-job limit", self.active_jobs_per_user),
            ("Shared global queue capacity", self.global_queue_capacity),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
