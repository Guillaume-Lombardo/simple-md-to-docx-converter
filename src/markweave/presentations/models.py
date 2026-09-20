"""Immutable, versioned presentation options shared by HTTP and durable workers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum

MAX_SLIDE_LEVEL = 6


class PresentationDialect(StrEnum):
    AUTO = "auto"
    MARKDOWN = "markdown"
    MARP = "marp"


@dataclass(frozen=True, slots=True)
class PresentationOptions:
    """No caller-selected executable, reader, filter, or template path is accepted."""

    dialect: PresentationDialect = PresentationDialect.AUTO
    slide_level: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.dialect, PresentationDialect):
            raise ValueError("Invalid presentation dialect")
        if (
            type(self.slide_level) is not int
            or not 1 <= self.slide_level <= MAX_SLIDE_LEVEL
        ):
            raise ValueError("Slide heading level must be between 1 and 6")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> PresentationOptions:
        try:
            raw = json.loads(value)
            if not isinstance(raw, dict) or set(raw) != {"dialect", "slide_level"}:
                raise ValueError
            return cls(PresentationDialect(raw["dialect"]), raw["slide_level"])
        except KeyError, TypeError, ValueError:
            raise ValueError("Invalid presentation options") from None
