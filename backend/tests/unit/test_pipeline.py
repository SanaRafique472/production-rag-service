import pytest

from app.errors import EmptyFileError, NoExtractableTextError, UnsupportedFileTypeError
from app.ingestion.chunking import BASELINE
from app.ingestion.pipeline import process_file
from app.ingestion.validation import FileType, sha256_hex


@pytest.mark.parametrize(
    ("key", "file_type"),
    [
        ("policy_pdf", FileType.PDF),
        ("handbook_docx", FileType.DOCX),
        ("slides_pptx", FileType.PPTX),
        ("notes_md", FileType.MD),
        ("urdu_txt", FileType.TXT),
    ],
)
def test_every_supported_type_produces_chunks(samples, settings, doc_id, key, file_type):
    path = samples[key]
    data = path.read_bytes()
    result = process_file(path.name, data, document_id=doc_id, settings=settings)
    assert result.file_type is file_type
    assert result.sha256 == sha256_hex(data) and result.size_bytes == len(data)
    assert result.chunks and all(c.text for c in result.chunks)


def test_scanned_pdf_raises_no_text(samples, settings, doc_id):
    with pytest.raises(NoExtractableTextError) as err:
        process_file(
            "scanned.pdf",
            samples["scanned_pdf"].read_bytes(),
            document_id=doc_id,
            settings=settings,
        )
    assert err.value.status_code == 400


def test_empty_and_fake_files(samples, settings, doc_id):
    with pytest.raises(EmptyFileError):
        process_file("empty.pdf", b"", document_id=doc_id, settings=settings)
    with pytest.raises(UnsupportedFileTypeError):
        process_file(
            "invoice.pdf", samples["fake_pdf"].read_bytes(), document_id=doc_id, settings=settings
        )


def test_filename_is_sanitised(samples, settings, doc_id):
    result = process_file(
        "../../leave_policy.pdf",
        samples["policy_pdf"].read_bytes(),
        document_id=doc_id,
        settings=settings,
    )
    assert result.filename == "leave_policy.pdf"
    assert all(c.filename == "leave_policy.pdf" for c in result.chunks)


def test_baseline_config_is_selectable(samples, settings, doc_id):
    result = process_file(
        "p.pdf",
        samples["policy_pdf"].read_bytes(),
        document_id=doc_id,
        config=BASELINE,
        settings=settings,
    )
    assert {c.chunking_config for c in result.chunks} == {"A"}
