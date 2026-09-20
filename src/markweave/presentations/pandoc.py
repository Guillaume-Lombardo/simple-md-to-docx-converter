"""Editable PowerPoint writer using the existing isolated Pandoc process adapter."""

from collections.abc import Mapping

from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
from markweave.presentations.models import MAX_SLIDE_LEVEL


class PandocPptxConverter(PandocDocxConverter):
    output_format = "pptx"
    required_parts = frozenset(
        {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"}
    )

    def __init__(
        self,
        config: PandocConfig,
        host_environment: Mapping[str, str],
        *,
        slide_level: int = 2,
    ) -> None:
        super().__init__(config, host_environment)
        if type(slide_level) is not int or not 0 <= slide_level <= MAX_SLIDE_LEVEL:
            raise ValueError("Invalid slide heading level")
        self.extra_arguments = (f"--slide-level={slide_level}",)
