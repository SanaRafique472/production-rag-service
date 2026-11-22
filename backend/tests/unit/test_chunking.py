from app.ingestion.chunking import BASELINE, PROPOSED, ChunkingConfig, chunk_document, count_tokens
from app.ingestion.extractors.base import TextUnit, rows_to_markdown


def _long_units() -> list[TextUnit]:
    """About 1,500 tokens of numbered sentences over two pages and two sections."""
    sentences = [
        f"Rule {i} says employees must follow procedure number {i} carefully." for i in range(120)
    ]
    return [
        TextUnit(" ".join(sentences[:60]), page=1, section="Rules > Part A"),
        TextUnit(" ".join(sentences[60:]), page=2, section="Rules > Part B"),
    ]


def test_metadata_on_every_chunk(doc_id):
    chunks = chunk_document(_long_units(), document_id=doc_id, filename="rules.pdf")
    assert [c.position for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.document_id == doc_id and c.filename == "rules.pdf"
        assert c.page_start is not None and c.section is not None
        assert c.chunking_config == "B"


def test_chunks_respect_size_limit(doc_id):
    for c in chunk_document(_long_units(), document_id=doc_id, filename="r.pdf"):
        assert c.token_count <= PROPOSED.size


def test_consecutive_chunks_overlap(doc_id):
    chunks = [
        c
        for c in chunk_document(_long_units(), document_id=doc_id, filename="r.pdf")
        if c.section == "Rules > Part A"
    ]
    assert len(chunks) >= 2
    last_sentence = chunks[0].text.rsplit(". ", 1)[-1]
    assert last_sentence in chunks[1].text  # the tail of chunk 0 is repeated in chunk 1
    assert not chunks[1].text.startswith("Rule 0 ")


def test_section_strategy_never_crosses_sections(doc_id):
    chunks = chunk_document(_long_units(), document_id=doc_id, filename="r.pdf")
    for c in chunks:
        assert not ("Rule 59 " in c.text and "Rule 60 " in c.text)
        assert c.page_start == c.page_end


def test_header_only_in_embed_text_for_proposed(doc_id):
    c = chunk_document(_long_units(), document_id=doc_id, filename="r.pdf")[0]
    assert c.embed_text.startswith("r.pdf > Rules > Part A\n\n")
    assert not c.text.startswith("r.pdf")
    base = chunk_document(_long_units(), document_id=doc_id, filename="r.pdf", config=BASELINE)[0]
    assert base.embed_text == base.text


def test_large_table_split_by_rows_with_header(doc_id):
    rows = [["Code", "Description"]] + [
        [f"C{i}", f"Item number {i} in the catalogue"] for i in range(80)
    ]
    unit = TextUnit(rows_to_markdown(rows), page=3, section="Catalogue", kind="table")
    small = ChunkingConfig("T", "section", 120, 0, False)
    chunks = chunk_document([unit], document_id=doc_id, filename="c.pdf", config=small)
    assert len(chunks) > 1
    for c in chunks:
        lines = c.text.split("\n")
        assert lines[0] == "| Code | Description |" and lines[1].startswith("| ---")
        assert all(line.startswith("| ") and line.endswith(" |") for line in lines)  # no cut rows
        assert c.kind == "table" and c.page_start == 3


def test_ids_are_deterministic(doc_id):
    a = chunk_document(_long_units(), document_id=doc_id, filename="r.pdf")
    b = chunk_document(_long_units(), document_id=doc_id, filename="r.pdf")
    assert [c.id for c in a] == [c.id for c in b]


def test_baseline_tracks_page_span(doc_id):
    paragraphs = [
        f"Paragraph {i}. " + "Staff must keep records accurate and complete. " * 18
        for i in range(6)
    ]
    units = [TextUnit(t, page=1 + i // 3, section="S") for i, t in enumerate(paragraphs)]
    chunks = chunk_document(units, document_id=doc_id, filename="r.pdf", config=BASELINE)
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2
    assert any(c.page_start == 1 and c.page_end == 2 for c in chunks)  # baseline crosses pages
    assert all(c.token_count <= BASELINE.size for c in chunks)


def test_urdu_sentences_split_on_urdu_full_stop(doc_id):
    text = "۔ ".join(["یہ ایک لمبا جملہ ہے جو بار بار دہرایا جاتا ہے"] * 40) + "۔"
    small = ChunkingConfig("U", "section", 100, 20, False)
    chunks = chunk_document([TextUnit(text)], document_id=doc_id, filename="u.txt", config=small)
    assert len(chunks) > 1 and all(c.token_count <= 100 for c in chunks)


def test_count_tokens():
    assert count_tokens("") == 0
    assert count_tokens("hello world") == 2
