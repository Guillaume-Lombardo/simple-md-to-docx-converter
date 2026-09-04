"""Stable content-free failures for the reverse-isolation broker."""

from enum import StrEnum


class BrokerErrorCategory(StrEnum):
    """Machine-readable failures that never contain runtime or document details."""

    AUTHENTICATION_FAILED = "authentication_failed"
    PROTOCOL_ERROR = "protocol_error"
    REPLAY_REJECTED = "replay_rejected"
    INVENTORY_FULL = "inventory_full"
    INVENTORY_FAILURE = "inventory_failure"
    RECONCILIATION_INCOMPLETE = "reconciliation_incomplete"
    TERMINATION_UNPROVEN = "termination_unproven"
    RUNTIME_FAILURE = "runtime_failure"


_MESSAGES: dict[BrokerErrorCategory, str] = {
    BrokerErrorCategory.AUTHENTICATION_FAILED: "Broker authentication failed.",
    BrokerErrorCategory.PROTOCOL_ERROR: "The broker protocol request is invalid.",
    BrokerErrorCategory.REPLAY_REJECTED: "The broker request was already observed.",
    BrokerErrorCategory.INVENTORY_FULL: "The broker inventory is at capacity.",
    BrokerErrorCategory.INVENTORY_FAILURE: "The broker inventory operation failed.",
    BrokerErrorCategory.RECONCILIATION_INCOMPLETE: (
        "Broker reconciliation is incomplete."
    ),
    BrokerErrorCategory.TERMINATION_UNPROVEN: (
        "The isolation unit termination could not be proven."
    ),
    BrokerErrorCategory.RUNTIME_FAILURE: "The isolation runtime operation failed.",
}


class BrokerError(RuntimeError):
    """A fixed-message broker failure safe for logs and protocol responses."""

    def __init__(self, category: BrokerErrorCategory) -> None:
        self.category = category
        self.message = _MESSAGES[category]
        super().__init__(self.message)
