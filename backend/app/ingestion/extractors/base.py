"""Shared data structures for all extractors.

Every extractor turns a file into an ordered list of ``TextUnit`` objects.
A unit is a paragraph-sized piece of text that remembers where it came from
(page or slide number, and the heading path it sits under). Keeping this
location data from the very first step is what makes citations possible.
"""

from dataclasses import dataclass, field
from typing import Literal

UnitKind = Literal["text", "table"]


@dataclass
class TextUnit:
    """A paragraph or table with its location in the source document."""

    text: str
    page: int | None = None  # 1-based page (PDF) or slide (PPTX); None for DOCX/TXT/MD
    section: str | None = None  # heading path, e.g. "Leave > Sick leave"
    kind: UnitKind = "text"


@dataclass
class ExtractedDocument:
    """Result of extracting one file."""

    units: list[TextUnit]
    page_count: int | None = None  # pages or slides; None when the format has no pages
    empty_pages: list[int] = field(default_factory=list)  # pages with no text (e.g. scans)


def rows_to_markdown(rows: list[list[str | None]]) -> str:
    """Render table rows as a Markdown table, first row as the header."""
    clean = [[(c or "").replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows]
    clean = [r for r in clean if any(r)]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    lines = ["| " + " | ".join(clean[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in clean[1:]]
    return "\n".join(lines)


class SectionTracker:
    """Keep the current heading path while walking a document top to bottom."""

    def __init__(self) -> None:
        """Start with no headings."""
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        """Enter a heading of ``level`` (1 = top), closing deeper or equal levels."""
        self._stack = [(lv, t) for lv, t in self._stack if lv < level]
        self._stack.append((level, title.strip()))

    @property
    def path(self) -> str | None:
        """Current path such as ``"Passwords > Multi-factor authentication"``."""
        return " > ".join(t for _, t in self._stack) or None
