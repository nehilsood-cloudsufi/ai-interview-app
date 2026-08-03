from app.services import data_scout_agent
from app.services.representative_verification import RepresentativeVerification


def _make_verification(**overrides) -> RepresentativeVerification:
    verification = RepresentativeVerification(
        verdict="VERIFIED",
        email_valid=True,
        email_type="corporate",
        email_domain="acme.example.com",
        email_domain_plausible=True,
        role="VP Sales",
        summary="Confirmed via public records.",
        background={"tenure": "3 years", "projects": ["Project X"], "focus_areas": ["sales"]},
        evidence=[{"url": "https://linkedin.com/in/janedoe", "source_name": "LinkedIn", "supports": "confirms VP Sales role at Acme Corp"}],
    )
    for key, value in overrides.items():
        setattr(verification, key, value)
    return verification


def _make_scout_result(**overrides) -> dict:
    result = {
        "internet_findings": "## Representative Verification\n\n`VERIFIED` Jane Doe — VP Sales, Acme Corp\n\n## Interview Claims\n\nNo claims required attention.",
        "interview_claims": [],
        "sources": {"blind_search": []},
        "findings_ok": True,
        "company_website": "https://acme.example.com",
        "representative_verification": _make_verification(),
        "claims_report": {"claims": [], "additional_findings": []},
    }
    result.update(overrides)
    return result


def test_scout_test_requires_company_name(client):
    response = client.post(
        "/api/scout-test",
        json={"company_name": "  ", "representative_name": "Jane Doe"},
    )
    assert response.status_code == 400
    assert "company_name" in response.json()["detail"]


def test_scout_test_happy_path_returns_full_result(client, monkeypatch):
    captured_args = {}

    async def fake_run_scout(company_name, representative_name, extra_context, documents, transcript, email=""):
        captured_args.update(
            company_name=company_name,
            representative_name=representative_name,
            extra_context=extra_context,
            documents=documents,
            transcript=transcript,
            email=email,
        )
        return _make_scout_result()

    monkeypatch.setattr(data_scout_agent, "run_scout", fake_run_scout)

    response = client.post(
        "/api/scout-test",
        json={
            "company_name": "Acme Corp",
            "representative_name": "Jane Doe",
            "email": "jane.doe@acme.example.com",
            "extra_context": "We are a widget maker.",
            "documents_text": "Company profile document text.",
            "transcript": "Interviewer: Tell me about your company.\nCandidate: We build widgets.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["internet_findings"].startswith("## Representative Verification")
    assert body["findings_ok"] is True
    assert body["company_website"] == "https://acme.example.com"
    assert body["representative_verification"]["verdict"] == "VERIFIED"
    assert body["representative_verification"]["background"]["projects"] == ["Project X"]

    # run_scout is called with the real positional signature, email plumbed
    # through, and the pasted document text wrapped into a one-item list.
    assert captured_args["company_name"] == "Acme Corp"
    assert captured_args["representative_name"] == "Jane Doe"
    assert captured_args["extra_context"] == "We are a widget maker."
    assert captured_args["documents"] == ["Company profile document text."]
    assert captured_args["transcript"] == "Interviewer: Tell me about your company.\nCandidate: We build widgets."
    assert captured_args["email"] == "jane.doe@acme.example.com"


def test_scout_test_blank_optional_fields_become_none_documents(client, monkeypatch):
    captured_args = {}

    async def fake_run_scout(company_name, representative_name, extra_context, documents, transcript, email=""):
        captured_args.update(extra_context=extra_context, documents=documents, transcript=transcript)
        return _make_scout_result()

    monkeypatch.setattr(data_scout_agent, "run_scout", fake_run_scout)

    response = client.post("/api/scout-test", json={"company_name": "Acme Corp"})

    assert response.status_code == 200
    assert captured_args["extra_context"] is None
    assert captured_args["documents"] is None
    assert captured_args["transcript"] is None


def test_scout_test_failure_returns_clean_error_not_a_stack_trace(client, monkeypatch):
    async def exploding_run_scout(*args, **kwargs):
        raise RuntimeError("GEMINI_API_KEY is not configured")

    monkeypatch.setattr(data_scout_agent, "run_scout", exploding_run_scout)

    response = client.post(
        "/api/scout-test", json={"company_name": "Acme Corp", "representative_name": "Jane Doe"}
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "GEMINI_API_KEY is not configured" in detail
    assert "Traceback" not in detail
