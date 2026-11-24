import io
import zipfile

import pytest

from app.config import Settings
from app.errors import CorruptFileError, EmptyFileError, FileTooLargeError, UnsupportedFileTypeError
from app.ingestion.validation import FileType, detect_file_type, safe_filename, sha256_hex


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("policy_pdf", FileType.PDF),
        ("handbook_docx", FileType.DOCX),
        ("slides_pptx", FileType.PPTX),
        ("notes_md", FileType.MD),
        ("urdu_txt", FileType.TXT),
    ],
)
def test_supported_files_are_detected(samples, settings, key, expected):
    path = samples[key]
    assert detect_file_type(path.name, path.read_bytes(), settings) is expected


def test_empty_file_rejected(samples, settings):
    with pytest.raises(EmptyFileError) as err:
        detect_file_type("empty.pdf", samples["empty_pdf"].read_bytes(), settings)
    assert err.value.status_code == 400


def test_oversized_file_rejected(settings):
    small = Settings(_env_file=None, max_upload_mb=1)
    data = b"%PDF-1.7\n" + b"0" * (2 * 1024 * 1024)
    with pytest.raises(FileTooLargeError) as err:
        detect_file_type("big.pdf", data, small)
    assert err.value.status_code == 413


def test_unknown_extension_rejected(settings):
    with pytest.raises(UnsupportedFileTypeError) as err:
        detect_file_type("setup.exe", b"MZ\x90\x00", settings)
    assert err.value.status_code == 415


def test_executable_renamed_to_pdf_rejected(samples, settings):
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("invoice.pdf", samples["fake_pdf"].read_bytes(), settings)


def test_random_bytes_named_docx_rejected(settings):
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("report.docx", b"not a zip at all", settings)


def test_zip_without_word_content_rejected(settings):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("report.docx", buf.getvalue(), settings)


def test_zip_bomb_rejected(settings):
    """A bomb is judged by how much it expands to (see test_word_formats for the ratio rule)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w/>")
        zf.writestr("word/padding.xml", "A" * 5_000_000)  # compresses ~1000:1
    with pytest.raises(CorruptFileError):
        detect_file_type("bomb.docx", buf.getvalue(), settings.model_copy(update={"max_zip_uncompressed_mb": 4}))


def test_binary_named_txt_rejected(settings):
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("notes.txt", b"\x7fELF\x02\x01\x01", settings)


def test_safe_filename_strips_paths_and_control_chars():
    assert safe_filename("../../etc/pass\x00wd.pdf") == "passwd.pdf"
    assert safe_filename("C:\\Users\\x\\report.docx") == "report.docx"


def test_sha256_is_deterministic():
    assert sha256_hex(b"abc") == sha256_hex(b"abc") != sha256_hex(b"abd")
