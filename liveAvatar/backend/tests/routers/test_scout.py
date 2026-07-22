import json

import httpx
import respx

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
TAVILY_BASE_URL = "https://api.tavily.com"


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
        return_value=httpx.Response(
            200,
            json={"results": [{"title": "Acme - LinkedIn", "url": "https://linkedin.com/company/acme", "content": "A company"}]},
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
        json={"company_name": "Acme Corp"},
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
    assert saved["findings_ok"] is True


@respx.mock
def test_scout_with_full_form_and_transcript(client, patch_settings, tmp_scout_dir):
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
    respx.post(f"{TAVILY_BASE_URL}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"claims": ["We have 50 employees"]})}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "## Internet Findings\n..."}}]}),
        ]
    )

    response = client.post(
        "/api/scout",
        json={
            "company_name": "Acme Corp",
            "company_website": "https://acme.example.com",
            "representative_name": "Jane Doe",
            "representative_role": "VP Sales",
            "transcript": "We have 50 employees.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interview_claims"] == ["We have 50 employees"]
    assert body["sources"]["pages"][0]["url"] == "https://acme.example.com"

    saved = client.get(f"/api/scout/{body['scout_id']}").json()
    assert saved["representative_name"] == "Jane Doe"
    assert saved["representative_role"] == "VP Sales"
    assert saved["interview_claims"] == ["We have 50 employees"]


@respx.mock
def test_scout_requires_company_name(client):
    response = client.post("/api/scout", json={})
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

    response = client.post("/api/scout", json={"company_name": "Doe & Associates Law Firm"})

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
        return_value=httpx.Response(
            200, json={"results": [{"title": "Acme overview", "url": "https://news.example.com/acme", "content": "..."}]}
        )
    )
    respx.get("https://news.example.com/acme").mock(return_value=httpx.Response(500))
    respx.post(f"{GEMINI_BASE_URL}chat/completions").mock(return_value=httpx.Response(429))

    response = client.post("/api/scout", json={"company_name": "Acme Corp"})

    assert response.status_code == 200
    body = response.json()
    assert body["findings_ok"] is False
    assert body["internet_findings"] == ""
    assert body["sources"]["blind_search"][0]["results"][0]["url"] == "https://news.example.com/acme"

    saved = client.get(f"/api/scout/{body['scout_id']}").json()
    assert saved["sources"]["blind_search"][0]["results"][0]["url"] == "https://news.example.com/acme"


@respx.mock
def test_scout_findings_failure_still_saves_sources(client, patch_settings, tmp_scout_dir):
    # No Gemini key -> synthesis raises -> findings_ok False, but gathered
    # sources must still be persisted (tolerant behavior, like transcripts).
    patch_settings(gemini_api_key=None, tavily_api_key=None, scout_local_dir=str(tmp_scout_dir), gcs_bucket=None)

    response = client.post("/api/scout", json={"company_name": "Acme Corp"})

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

    response = client.post("/api/scout", json={"company_name": "Acme Corp"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save scout report"
