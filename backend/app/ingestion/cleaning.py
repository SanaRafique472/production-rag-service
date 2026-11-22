r"""Text cleaning applied after extraction and before chunking.

Steps (each one is small and testable):
1. Unicode NFKC normalisation (unifies look-alike characters, keeps Urdu/Arabic).
2. Join words split by a hyphen at a line break ("por-\\ntal" -> "portal").
3. Turn single line breaks inside a paragraph into spaces; tables keep theirs.
4. Collapse repeated spaces.
5. Drop page-number lines and running headers/footers that repeat on most pages.
"""

import re
import unicodedata
from collections import Counter, defaultdict

from app.ingestion.extractors.base import TextUnit

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t ]+")
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.IGNORECASE)


def normalise(text: str, keep_newlines: bool) -> str:
    """Normalise Unicode, fix hyphenation and whitespace in one unit of text."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    if not keep_newlines:
        text = re.sub(r"\s*\n\s*", " ", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _signature(text: str) -> str:
    """Make a short line comparable: lower-case, digits replaced by '#'."""
    return re.sub(r"\d+", "#", text.lower()).strip()


def remove_repeated_lines(units: list[TextUnit], page_count: int | None) -> list[TextUnit]:
    """Drop page numbers and short units repeated on at least 60% of pages.

    Repetition is only judged for documents with 3+ pages; with fewer pages a
    repeated line could just be ordinary content.
    """
    units = [u for u in units if not _PAGE_NUMBER.match(u.text)]
    if not page_count or page_count < 3:
        return units
    pages_by_sig: dict[str, set[int]] = defaultdict(set)
    for u in units:
        if u.page is not None and len(u.text) <= 100 and u.kind == "text":
            pages_by_sig[_signature(u.text)].add(u.page)
    repeated = {sig for sig, pages in pages_by_sig.items() if len(pages) >= 0.6 * page_count}
    return [u for u in units if not (u.kind == "text" and len(u.text) <= 100 and _signature(u.text) in repeated)]


def clean_units(units: list[TextUnit], page_count: int | None) -> list[TextUnit]:
    """Apply every cleaning step and drop units that end up empty."""
    cleaned = [
        TextUnit(
            text=normalise(u.text, keep_newlines=u.kind == "table"),
            page=u.page,
            section=normalise(u.section, keep_newlines=False) if u.section else None,
            kind=u.kind,
        )
        for u in units
    ]
    cleaned = [u for u in cleaned if u.text]
    return remove_repeated_lines(cleaned, page_count)


def text_stats(units: list[TextUnit]) -> Counter:
    """Small summary used by notebooks and logs: units and characters by kind."""
    stats: Counter = Counter()
    for u in units:
        stats[f"{u.kind}_units"] += 1
        stats[f"{u.kind}_chars"] += len(u.text)
    return stats
