"""Chunking strategies.

Two strategies exist so the evaluation can compare them:

* ``section`` (configuration B, proposed): never crosses a heading boundary,
  packs whole sentences up to ~400 tokens, carries ~60 tokens of overlap,
  keeps tables as their own chunks (split by rows, header repeated) and
  prepends "file > section" to the text that gets embedded.
* ``fixed`` (configuration A, baseline): LangChain's
  RecursiveCharacterTextSplitter over the whole document at 800/120 tokens,
  ignoring structure. This is the common default many tutorials use.

Token counts use OpenAI's ``cl100k_base`` tokenizer as a stable,
provider-neutral measure of length.
"""

import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingestion.extractors.base import TextUnit

_SENTENCE_END = re.compile(r"(?<=[.!?۔؟])\s+")  # includes Urdu '۔' and Arabic '؟'


@dataclass(frozen=True)
class ChunkingConfig:
    """Parameters of one chunking strategy."""

    name: str
    strategy: Literal["section", "fixed"]
    size: int
    overlap: int
    add_header: bool


PROPOSED = ChunkingConfig("B", "section", 400, 60, True)
BASELINE = ChunkingConfig("A", "fixed", 800, 120, False)


@dataclass
class Chunk:
    """One retrievable piece of a document, with all citation metadata."""

    id: str
    document_id: str
    filename: str
    position: int  # 0-based order within the document
    text: str  # shown to users and sent to the LLM
    embed_text: str  # text that is embedded (may include the header)
    page_start: int | None
    page_end: int | None
    section: str | None
    token_count: int
    kind: Literal["text", "table"]
    chunking_config: str


@lru_cache
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count the tokens in ``text``."""
    return len(_encoder().encode(text))


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    """Split one over-long sentence into token windows."""
    ids = _encoder().encode(text)
    step = max(size - overlap, 1)
    return [_encoder().decode(ids[i : i + size]) for i in range(0, len(ids), step)]


@dataclass
class _Piece:
    text: str
    page: int | None
    new_paragraph: bool
    tokens: int


def _pieces(units: list[TextUnit], size: int, overlap: int) -> list[_Piece]:
    """Break text units into sentence pieces that remember their page."""
    out: list[_Piece] = []
    for unit in units:
        first = True
        for sentence in _SENTENCE_END.split(unit.text):
            if not sentence.strip():
                continue
            parts = (
                [sentence]
                if count_tokens(sentence) <= size
                else _split_long(sentence, size, overlap)
            )
            for part in parts:
                out.append(_Piece(part, unit.page, first, count_tokens(part)))
                first = False
    return out


def _join(pieces: list[_Piece]) -> str:
    text = ""
    for i, p in enumerate(pieces):
        sep = "" if i == 0 else ("\n\n" if p.new_paragraph else " ")
        text += sep + p.text
    return text


def _pack(pieces: list[_Piece], size: int, overlap: int) -> list[list[_Piece]]:
    """Greedily pack sentences into chunks of at most ``size`` tokens, with overlap."""
    chunks: list[list[_Piece]] = []
    current: list[_Piece] = []
    tokens = 0
    for piece in pieces:
        if current and tokens + piece.tokens > size:
            chunks.append(current)
            tail: list[_Piece] = []
            tail_tokens = 0
            for prev in reversed(current):  # carry the last sentences as overlap
                if tail_tokens + prev.tokens > overlap:
                    break
                tail.insert(0, prev)
                tail_tokens += prev.tokens
            current, tokens = tail, tail_tokens
        current.append(piece)
        tokens += piece.tokens
    if current:
        chunks.append(current)
    return chunks


def _split_table(md: str, size: int) -> list[str]:
    """Split a Markdown table by rows, repeating the header in each part."""
    lines = md.split("\n")
    header, rows = lines[:2], lines[2:]
    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        if current and count_tokens("\n".join(header + current + [row])) > size:
            parts.append("\n".join(header + current))
            current = []
        current.append(row)
    if current or not parts:
        parts.append("\n".join(header + current))
    return parts


def _group_by_section(units: list[TextUnit]) -> list[list[TextUnit]]:
    groups: list[list[TextUnit]] = []
    for u in units:
        if groups and groups[-1][-1].section == u.section:
            groups[-1].append(u)
        else:
            groups.append([u])
    return groups


def _section_chunks(
    units: list[TextUnit], cfg: ChunkingConfig
) -> list[tuple[str, int | None, int | None, str | None, str]]:
    """Return (text, page_start, page_end, section, kind) tuples for strategy 'section'."""
    out: list[tuple[str, int | None, int | None, str | None, str]] = []
    for group in _group_by_section(units):
        section = group[0].section
        buffer: list[TextUnit] = []
        for unit in group:
            if unit.kind == "table":  # text before a table is chunked first, keeping order
                out += _text_chunks(buffer, section, cfg)
                buffer = []
                out += [
                    (p, unit.page, unit.page, section, "table")
                    for p in _split_table(unit.text, cfg.size)
                ]
            else:
                buffer.append(unit)
        out += _text_chunks(buffer, section, cfg)
    return out


def _text_chunks(
    units: list[TextUnit], section: str | None, cfg: ChunkingConfig
) -> list[tuple[str, int | None, int | None, str | None, str]]:
    """Pack the sentences of consecutive text units (one section) into chunks."""
    out = []
    for packed in _pack(_pieces(units, cfg.size, cfg.overlap), cfg.size, cfg.overlap):
        pages = [p.page for p in packed if p.page is not None]
        out.append(
            (_join(packed), min(pages, default=None), max(pages, default=None), section, "text")
        )
    return out


def _fixed_chunks(
    units: list[TextUnit], cfg: ChunkingConfig
) -> list[tuple[str, int | None, int | None, str | None, str]]:
    """Baseline: one recursive split over the whole document text."""
    full, spans = "", []  # spans: (start_offset, unit)
    for u in units:
        if full:
            full += "\n\n"
        spans.append((len(full), u))
        full += u.text
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=cfg.size,
        chunk_overlap=cfg.overlap,
        add_start_index=True,
    )

    def unit_at(offset: int) -> TextUnit:
        found = spans[0][1]
        for start, u in spans:
            if start > offset:
                break
            found = u
        return found

    out = []
    for doc in splitter.create_documents([full]):
        start = doc.metadata["start_index"]
        first, last = unit_at(start), unit_at(start + len(doc.page_content) - 1)
        out.append((doc.page_content, first.page, last.page, first.section, "text"))
    return out


def chunk_document(
    units: list[TextUnit], *, document_id: str, filename: str, config: ChunkingConfig = PROPOSED
) -> list[Chunk]:
    """Split cleaned units into chunks carrying full citation metadata.

    Chunk ids are deterministic (UUID5 of document id + config + position), so
    re-ingesting the same document gives the same ids.
    """
    if not units:
        return []
    raw = (
        _section_chunks(units, config)
        if config.strategy == "section"
        else _fixed_chunks(units, config)
    )
    namespace = uuid.UUID(document_id)
    chunks: list[Chunk] = []
    for position, (text, p_start, p_end, section, kind) in enumerate(raw):
        header = f"{filename} > {section}" if section else filename
        embed_text = f"{header}\n\n{text}" if config.add_header else text
        chunks.append(
            Chunk(
                id=str(uuid.uuid5(namespace, f"{config.name}:{position}")),
                document_id=document_id,
                filename=filename,
                position=position,
                text=text,
                embed_text=embed_text,
                page_start=p_start,
                page_end=p_end,
                section=section,
                token_count=count_tokens(text),
                kind=kind,  # type: ignore[arg-type]
                chunking_config=config.name,
            )
        )
    return chunks
