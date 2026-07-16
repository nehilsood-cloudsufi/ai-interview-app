import io

import docx
import pymupdf

from app.config import settings


def extract_text(filename: str, contents: bytes) -> str:
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        return _extract_pdf_text(filename, contents)
    if lower_name.endswith(".docx"):
        return _extract_docx_text(contents)
    if lower_name.endswith(".txt"):
        return contents.decode("utf-8", errors="ignore") + "\n"
    raise ValueError(
        f"Unsupported file format: {filename}. Only PDF, DOCX, and TXT allowed."
    )


def _extract_pdf_text(filename: str, contents: bytes) -> str:
    doc = pymupdf.open(stream=contents, filetype="pdf")
    if len(doc) > settings.max_pdf_pages:
        raise ValueError(f"File {filename} exceeds {settings.max_pdf_pages} page limit")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    return text


def _extract_docx_text(contents: bytes) -> str:
    doc = docx.Document(io.BytesIO(contents))
    text = ""
    for para in doc.paragraphs:
        text += para.text + "\n"
    return text
