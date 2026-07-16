import httpx
import respx

BASE_URL = "https://api.liveavatar.com/v1"


def _txt_file(name="resume.txt", content=b"Experienced engineer."):
    return ("files", (name, content, "text/plain"))


@respx.mock
def test_upload_resume_happy_path_txt(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-1"}})
    )

    response = client.post("/api/upload-resume", files=[_txt_file()])

    assert response.status_code == 200
    assert response.json() == {"context_id": "ctx-1"}


@respx.mock
def test_upload_resume_too_many_files(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", max_files=1)

    response = client.post(
        "/api/upload-resume",
        files=[_txt_file("a.txt"), _txt_file("b.txt")],
    )

    assert response.status_code == 400
    assert "Maximum 1 files allowed" in response.json()["detail"]


@respx.mock
def test_upload_resume_oversized_file(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", max_file_size_bytes=10)

    response = client.post(
        "/api/upload-resume",
        files=[_txt_file("big.txt", b"x" * 100)],
    )

    assert response.status_code == 400
    assert "exceeds 5MB limit" in response.json()["detail"]


@respx.mock
def test_upload_resume_unsupported_format_collapses_to_generic_400(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key")

    response = client.post(
        "/api/upload-resume",
        files=[("files", ("resume.exe", b"binary junk", "application/octet-stream"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to read resume.exe"


@respx.mock
def test_upload_resume_pdf_page_limit_collapses_to_generic_400(client, patch_settings):
    import pymupdf

    patch_settings(liveavatar_api_key="live-key", max_pdf_pages=1)
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    response = client.post(
        "/api/upload-resume",
        files=[("files", ("resume.pdf", pdf_bytes, "application/pdf"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to read resume.pdf"


@respx.mock
def test_upload_resume_missing_api_key(client, patch_settings):
    patch_settings(liveavatar_api_key=None)

    response = client.post("/api/upload-resume", files=[_txt_file()])

    assert response.status_code == 500
    assert response.json()["detail"] == "LiveAvatar API Key missing"


@respx.mock
def test_upload_resume_liveavatar_http_error_passthrough(client, patch_settings):
    patch_settings(liveavatar_api_key="live-key", liveavatar_base_url=BASE_URL)
    respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )

    response = client.post("/api/upload-resume", files=[_txt_file()])

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to create context"


@respx.mock
def test_upload_resume_request_api_key_overrides_settings(client, patch_settings):
    patch_settings(liveavatar_api_key=None, liveavatar_base_url=BASE_URL)
    route = respx.post(f"{BASE_URL}/contexts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ctx-1"}})
    )

    response = client.post(
        "/api/upload-resume",
        files=[_txt_file()],
        data={"api_key": "request-key"},
    )

    assert response.status_code == 200
    assert route.calls[0].request.headers["x-api-key"] == "request-key"
