"""Plain text and Markdown extraction.

The encoding is detected (UTF-8, UTF-16, Windows code pages, ...) so that
Urdu or Arabic text files are read correctly. Markdown headings (``#``)
become sections; blank lines separate paragraphs.
"""

import re

from charset_normalizer import from_bytes

from app.errors import UnsupportedFileTypeError
from app.ingestion.extractors.base import ExtractedDocument, SectionTracker, TextUnit

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def decode_text(data: bytes) -> str:
    """Decode bytes to str using detected encoding; reject undecodable input."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            raise UnsupportedFileTypeError(
                "Could not detect the text encoding of this file."
            ) from None
        return str(best)


def extract_text(data: bytes, markdown: bool) -> ExtractedDocument:
    """Split a text or Markdown file into paragraph units with sections."""
    text = decode_text(data)
    sections = SectionTracker()
    units: list[TextUnit] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer and "".join(buffer).strip():
            units.append(TextUnit(text="\n".join(buffer).strip(), section=sections.path))
        buffer.clear()

    for line in text.splitlines():
        heading = _MD_HEADING.match(line) if markdown else None
        if heading:
            flush()
            sections.push(len(heading.group(1)), heading.group(2))
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return ExtractedDocument(units=units, page_count=None)
