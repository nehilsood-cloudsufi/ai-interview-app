import io

import docx
import pymupdf
import pytest

from app.services import resume_parser


def _make_pdf_bytes(num_pages: int = 1, text: str = "Hello world") -> bytes:
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_extract_txt():
    result = resume_parser.extract_text("resume.txt", b"Hello candidate")
    assert result == "Hello candidate\n"


def test_extract_txt_case_insensitive_extension():
    result = resume_parser.extract_text("resume.TXT", b"Hello candidate")
    assert result == "Hello candidate\n"


def test_extract_txt_ignores_invalid_utf8():
    result = resume_parser.extract_text("resume.txt", b"\xff\xfeHello")
    assert "Hello" in result


def test_extract_pdf():
    pdf_bytes = _make_pdf_bytes(num_pages=1, text="Candidate Resume")
    result = resume_parser.extract_text("resume.pdf", pdf_bytes)
    assert "Candidate Resume" in result


def test_extract_pdf_case_insensitive_extension():
    pdf_bytes = _make_pdf_bytes(num_pages=1, text="Candidate Resume")
    result = resume_parser.extract_text("resume.PDF", pdf_bytes)
    assert "Candidate Resume" in result


def test_extract_pdf_over_page_limit_raises(patch_settings):
    patch_settings(max_pdf_pages=2)
    pdf_bytes = _make_pdf_bytes(num_pages=3, text="Page")
    with pytest.raises(ValueError, match="page limit"):
        resume_parser.extract_text("resume.pdf", pdf_bytes)


def test_extract_pdf_at_page_limit_ok(patch_settings):
    patch_settings(max_pdf_pages=3)
    pdf_bytes = _make_pdf_bytes(num_pages=3, text="Page")
    result = resume_parser.extract_text("resume.pdf", pdf_bytes)
    assert "Page" in result


def test_extract_docx():
    docx_bytes = _make_docx_bytes(["Line one", "Line two"])
    result = resume_parser.extract_text("resume.docx", docx_bytes)
    assert "Line one" in result
    assert "Line two" in result


def test_extract_docx_case_insensitive_extension():
    docx_bytes = _make_docx_bytes(["Line one"])
    result = resume_parser.extract_text("resume.DOCX", docx_bytes)
    assert "Line one" in result


def test_extract_unsupported_format_raises():
    with pytest.raises(ValueError, match="Unsupported file format"):
        resume_parser.extract_text("resume.exe", b"whatever")
