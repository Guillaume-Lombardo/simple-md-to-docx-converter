"""Immutable, storage-neutral reverse extraction options."""

from dataclasses import dataclass
from enum import StrEnum
from typing import cast

PPTX_EXTRACTOR = "markweave-pptx-v1"


class ReverseExtraction(StrEnum):
    """Explicit opt-in alternatives to the original anydoc extraction."""

    ANYDOC = "anydoc"
    SLIDES = "slides"
    MARP = "marp"


@dataclass(frozen=True, slots=True)
class ReversionOptions:
    """Canonical options included in durable request identity."""

    extraction: ReverseExtraction = ReverseExtraction.ANYDOC
    include_notes: bool = True
    include_images: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.extraction) is not ReverseExtraction
            or type(self.include_notes) is not bool
            or type(self.include_images) is not bool
            or (
                self.extraction is ReverseExtraction.ANYDOC
                and (not self.include_notes or not self.include_images)
            )
        ):
            raise ValueError("Reverse extraction options are invalid")

    def to_dict(self) -> dict[str, str | bool]:
        """Return the closed JSON-compatible option object."""

        return {
            "extraction": self.extraction.value,
            "include_notes": self.include_notes,
            "include_images": self.include_images,
        }

    @classmethod
    def from_dict(cls, value: object) -> ReversionOptions:
        """Decode options without coercing booleans or ignoring unknown fields."""

        if type(value) is not dict or value.keys() != {
            "extraction",
            "include_notes",
            "include_images",
        }:
            raise ValueError("Reverse extraction options are invalid")
        fields = cast(dict[str, object], value)
        extraction = fields["extraction"]
        notes, images = fields["include_notes"], fields["include_images"]
        if (
            type(extraction) is not str
            or type(notes) is not bool
            or type(images) is not bool
        ):
            raise ValueError("Reverse extraction options are invalid")
        return cls(ReverseExtraction(extraction), notes, images)
