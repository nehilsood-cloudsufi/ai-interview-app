def _txt_file(name="notes.txt", content=b"Some vendor doc text."):
    return ("files", (name, content, "text/plain"))


def test_vendor_profile_happy_path_with_files(client):
    response = client.post(
        "/api/vendor-profile",
        data={
            "company_name": "Acme Corp",
            "website": "https://acme.example",
            "contact_name": "Jane Doe",
            "contact_role": "CTO",
        },
        files=[_txt_file()],
    )

    assert response.status_code == 200
    body = response.json()
    assert "interview_id" in body
    assert isinstance(body["interview_id"], str) and body["interview_id"]


def test_vendor_profile_happy_path_without_files(client):
    response = client.post(
        "/api/vendor-profile",
        data={
            "company_name": "Acme Corp",
            "contact_name": "Jane Doe",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "interview_id" in body
    assert isinstance(body["interview_id"], str) and body["interview_id"]


def test_vendor_profile_stores_interview_state(client):
    from app.services import interview_state

    response = client.post(
        "/api/vendor-profile",
        data={
            "company_name": "Acme Corp",
            "website": "https://acme.example",
            "contact_name": "Jane Doe",
            "contact_role": "CTO",
        },
        files=[_txt_file(content=b"Doc contents here.")],
    )

    interview_id = response.json()["interview_id"]
    state = interview_state.get(interview_id)
    assert state is not None
    assert state.vendor_profile.company_name == "Acme Corp"
    assert state.vendor_profile.website == "https://acme.example"
    assert state.vendor_profile.contact_name == "Jane Doe"
    assert state.vendor_profile.contact_role == "CTO"
    assert "Doc contents here." in state.vendor_profile.doc_text


def test_vendor_profile_stores_empty_doc_text_without_files(client):
    from app.services import interview_state

    response = client.post(
        "/api/vendor-profile",
        data={"company_name": "Acme Corp", "contact_name": "Jane Doe"},
    )

    interview_id = response.json()["interview_id"]
    state = interview_state.get(interview_id)
    assert state.vendor_profile.doc_text == ""
    assert state.vendor_profile.website is None
    assert state.vendor_profile.contact_role is None


def test_vendor_profile_missing_company_name_422(client):
    response = client.post(
        "/api/vendor-profile",
        data={"contact_name": "Jane Doe"},
    )

    assert response.status_code == 422


def test_vendor_profile_missing_contact_name_422(client):
    response = client.post(
        "/api/vendor-profile",
        data={"company_name": "Acme Corp"},
    )

    assert response.status_code == 422


def test_vendor_profile_too_many_files(client, patch_settings):
    patch_settings(max_files=1)

    response = client.post(
        "/api/vendor-profile",
        data={"company_name": "Acme Corp", "contact_name": "Jane Doe"},
        files=[_txt_file("a.txt"), _txt_file("b.txt")],
    )

    assert response.status_code == 400
    assert "Maximum 1 files allowed" in response.json()["detail"]


def test_vendor_profile_oversized_file(client, patch_settings):
    patch_settings(max_file_size_bytes=10)

    response = client.post(
        "/api/vendor-profile",
        data={"company_name": "Acme Corp", "contact_name": "Jane Doe"},
        files=[_txt_file("big.txt", b"x" * 100)],
    )

    assert response.status_code == 400
    assert "exceeds 5MB limit" in response.json()["detail"]


def test_vendor_profile_unsupported_format_collapses_to_generic_400(client):
    response = client.post(
        "/api/vendor-profile",
        data={"company_name": "Acme Corp", "contact_name": "Jane Doe"},
        files=[("files", ("vendor.exe", b"binary junk", "application/octet-stream"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Failed to read vendor.exe"
