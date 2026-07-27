import json

import httpx
import respx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
TAVILY_BASE_URL = "https://api.tavily.com"


def _tavily_handler(responses=None, default=None):
    """Builds a respx side_effect that returns different Tavily results per
    query (queries differ per test - blind search, official-website
    fallback, representative search, targeted search - so a single fixed
    mock response can't distinguish them)."""
    responses = responses or {}
    default = default if default is not None else []

    def _handle(request):
        body = json.loads(request.content)
        results = responses.get(body.get("query", ""), default)
        return httpx.Response(200, json={"results": results})

    return _handle


@respx.mock
def test_scout_happy_path(client, patch_settings, tmp_scout_dir):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        github_api_token=None,
        scout_local_dir=str(tmp_scout_dir),
        gcs_bucket=None,
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        side_effect=_tavily_handler(
            {"Acme Corp overview": [{"title": "Acme - LinkedIn", "url": "https://linkedin.com/company/acme", "content": "A company"}]}
        )
    )
    respx.get("https://linkedin.com/company/acme").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<main>Full profile text</main>")
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]})
    )

    response = client.post(
        "/api/scout",
        json={"company_name": "Acme Corp", "representative_name": "Jane Doe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["internet_findings"] == "## Internet Findings\n..."
    assert body["interview_claims"] == []
    assert body["findings_ok"] is True
    assert body["scout_id"]
    assert body["sources"]["blind_search"][0]["results"][0]["url"] == "https://linkedin.com/company/acme"

    get_response = client.get(f"/api/scout/{body['scout_id']}")
    assert get_response.status_code == 200
    saved = get_response.json()
    assert saved["company_name"] == "Acme Corp"
    assert saved["representative_name"] == "Jane Doe"
    assert saved["findings_ok"] is True


@respx.mock
def test_scout_derives_website_from_extra_context_and_uses_transcript(client, patch_settings, tmp_scout_dir):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        github_api_token=None,
        scout_local_dir=str(tmp_scout_dir),
        gcs_bucket=None,
    )
    respx.get("https://acme.example.com").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text="<main>Acme Corp builds widgets.</main>")
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(side_effect=_tavily_handler())
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We have 50 employees", "search_query": "Acme Corp 50 employees", "source": "transcript"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]}),
        ]
    )

    response = client.post(
        "/api/scout",
        json={
            "company_name": "Acme Corp",
            "representative_name": "Jane Doe",
            "representative_role": "VP Sales",
            "extra_context": "Find us at https://acme.example.com",
            "transcript": "We have 50 employees.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interview_claims"] == ["We have 50 employees"]
    assert body["sources"]["pages"][0]["url"] == "https://acme.example.com"

    saved = client.get(f"/api/scout/{body['scout_id']}").json()
    assert saved["company_website"] == "https://acme.example.com"
    assert saved["representative_name"] == "Jane Doe"
    assert saved["representative_role"] == "VP Sales"
    assert saved["interview_claims"] == ["We have 50 employees"]


@respx.mock
def test_scout_extracts_claims_from_documents_before_interview(client, patch_settings, tmp_scout_dir):
    # Pre-interview: no transcript yet, only attached documents.
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        tavily_api_key=None,
        scout_local_dir=str(tmp_scout_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": [{"claim": "We were founded in 2010", "search_query": "Acme founded 2010", "source": "document"}]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]}),
        ]
    )

    response = client.post(
        "/api/scout",
        json={
            "company_name": "Acme Corp",
            "representative_name": "Jane Doe",
            "documents": ["Company profile: Acme Corp was founded in 2010."],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interview_claims"] == ["We were founded in 2010"]


def test_scout_requires_company_name(client):
    response = client.post("/api/scout", json={"representative_name": "Jane Doe"})
    assert response.status_code == 422


def test_scout_requires_representative_name(client):
    response = client.post("/api/scout", json={"company_name": "Acme Corp"})
    assert response.status_code == 422


@respx.mock
def test_scout_omits_github_key_for_non_technical_company(client, patch_settings, tmp_scout_dir):
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        tavily_api_key=None,
        scout_local_dir=str(tmp_scout_dir),
        gcs_bucket=None,
    )
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]})
    )

    response = client.post(
        "/api/scout",
        json={"company_name": "Doe & Associates Law Firm", "representative_name": "John Doe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "github" not in body["sources"]


@respx.mock
def test_scout_synthesis_failure_preserves_already_gathered_sources(client, patch_settings, tmp_scout_dir):
    # The exact regression this guards against: web-search data costs a real
    # API call to gather - a later synthesis failure (e.g. Gemini quota) must
    # not discard it.
    patch_settings(
        gemini_api_key="gem-key",
        gemini_base_url=GEMINI_BASE_URL,
        tavily_api_key="tvly-key",
        tavily_api_base_url=TAVILY_BASE_URL,
        scout_local_dir=str(tmp_scout_dir),
        gcs_bucket=None,
    )
    respx.post(f"{TAVILY_BASE_URL}/search").mock(
        side_effect=_tavily_handler(
            {"Acme Corp overview": [{"title": "Acme overview", "url": "https://news.example.com/acme", "content": "..."}]}
        )
    )
    respx.get("https://news.example.com/acme").mock(return_value=httpx.Response(500))
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(429))

    response = client.post(
        "/api/scout",
        json={"company_name": "Acme Corp", "representative_name": "Jane Doe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["findings_ok"] is False
    assert body["internet_findings"] == ""
    assert body["sources"]["blind_search"][0]["results"][0]["url"] == "https://news.example.com/acme"

    saved = client.get(f"/api/scout/{body['scout_id']}").json()
    assert saved["sources"]["blind_search"][0]["results"][0]["url"] == "https://news.example.com/acme"


def test_scout_findings_failure_still_saves_sources(client, patch_settings, tmp_scout_dir):
    # No Gemini key -> synthesis raises -> findings_ok False, but gathered
    # sources must still be persisted (tolerant behavior, like transcripts).
    patch_settings(gemini_api_key=None, tavily_api_key=None, scout_local_dir=str(tmp_scout_dir), gcs_bucket=None)

    response = client.post(
        "/api/scout",
        json={"company_name": "Acme Corp", "representative_name": "Jane Doe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["findings_ok"] is False
    assert body["internet_findings"] == ""
    # The bug being guarded against: a synthesis failure must not wipe out
    # sources that were actually gathered (empty here since no website/
    # transcript/API keys, but the response shape must stay intact).
    assert body["sources"] == {}

    get_response = client.get(f"/api/scout/{body['scout_id']}")
    assert get_response.status_code == 200
    assert get_response.json()["findings_ok"] is False


def test_scout_report_not_found_returns_404(client, tmp_scout_dir):
    response = client.get("/api/scout/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Scout report not found"


def test_scout_save_failure_returns_500(client, patch_settings, monkeypatch):
    patch_settings(gemini_api_key=None, tavily_api_key=None)

    async def _boom(scout_id, payload):
        raise OSError("disk full")

    from app.services import scout_store

    monkeypatch.setattr(scout_store, "save", _boom)

    response = client.post(
        "/api/scout",
        json={"company_name": "Acme Corp", "representative_name": "Jane Doe"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save scout report"


# --- POST /api/scout/document: standalone document text extraction --------


def _upload_document(client, filename: str, content: bytes):
    return client.post(
        "/api/scout/document",
        files={"file": (filename, content, "application/octet-stream")},
    )


def test_extract_txt_document(client):
    response = _upload_document(client, "profile.txt", b"Acme Corp ships data pipelines for banks.")

    assert response.status_code == 200
    assert response.json() == {"text": "Acme Corp ships data pipelines for banks."}


def test_extract_docx_document(client):
    import io

    import docx

    document = docx.Document()
    document.add_paragraph("Acme builds reusable rockets for cargo.")
    buffer = io.BytesIO()
    document.save(buffer)

    response = _upload_document(client, "company.docx", buffer.getvalue())

    assert response.status_code == 200
    assert response.json() == {"text": "Acme builds reusable rockets for cargo."}


def test_extract_document_rejects_unsupported_extension(client):
    response = _upload_document(client, "notes.exe", b"whatever")

    assert response.status_code == 400
    assert "Unsupported document type" in response.json()["detail"]


def test_extract_document_rejects_empty_text(client):
    response = _upload_document(client, "empty.txt", b"   ")

    assert response.status_code == 400
    assert response.json()["detail"] == "Document appears to be empty"


def test_extract_document_rejects_oversize_file(client):
    content = b"x" * (10 * 1024 * 1024 + 1)

    response = _upload_document(client, "big.txt", content)

    assert response.status_code == 413
    assert response.json()["detail"] == "Document is too large"


def test_extract_document_unreadable_pdf_returns_400(client):
    response = _upload_document(client, "broken.pdf", b"not a real pdf")

    assert response.status_code == 400
    assert response.json()["detail"] == "Could not read the document"
