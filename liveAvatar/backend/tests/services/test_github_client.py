import httpx
import respx

from app.services import github_client

BASE_URL = "https://api.github.com"


def test_derive_username_from_profile_url():
    assert github_client.derive_username("https://github.com/janedoe") == "janedoe"


def test_derive_username_from_repo_url():
    assert github_client.derive_username("https://github.com/janedoe/my-repo") == "janedoe"


def test_derive_username_returns_none_for_non_profile_paths():
    assert github_client.derive_username("https://github.com/orgs/acme") is None


def test_derive_username_returns_none_when_no_match():
    assert github_client.derive_username("https://example.com") is None


@respx.mock
async def test_fetch_profile_happy_path_includes_readme_excerpt(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/janedoe").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Jane Doe",
                "bio": "Engineer",
                "company": "Acme",
                "location": "Remote",
                "public_repos": 12,
                "followers": 34,
                "blog": "https://janedoe.dev",
                "html_url": "https://github.com/janedoe",
                "hireable": True,
                "created_at": "2015-01-01T00:00:00Z",
            },
        )
    )
    respx.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "name": "cool-repo",
                        "full_name": "janedoe/cool-repo",
                        "description": "A cool repo",
                        "language": "Python",
                        "stargazers_count": 5,
                        "html_url": "https://github.com/janedoe/cool-repo",
                    }
                ]
            },
        )
    )
    respx.get(f"{BASE_URL}/repos/janedoe/cool-repo/readme").mock(
        return_value=httpx.Response(200, text="# Cool Repo\nDoes cool things.")
    )

    profile = await github_client.fetch_profile("janedoe")

    assert profile["username"] == "janedoe"
    assert profile["name"] == "Jane Doe"
    assert profile["hireable"] is True
    assert profile["created_at"] == "2015-01-01T00:00:00Z"
    assert profile["repos"] == [
        {
            "name": "cool-repo",
            "description": "A cool repo",
            "language": "Python",
            "stargazers_count": 5,
            "html_url": "https://github.com/janedoe/cool-repo",
            "readme_excerpt": "# Cool Repo\nDoes cool things.",
        }
    ]


@respx.mock
async def test_fetch_profile_missing_readme_does_not_fail_profile(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/janedoe").mock(return_value=httpx.Response(200, json={"public_repos": 1}))
    respx.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"name": "no-readme", "full_name": "janedoe/no-readme", "stargazers_count": 0}]},
        )
    )
    respx.get(f"{BASE_URL}/repos/janedoe/no-readme/readme").mock(return_value=httpx.Response(404))

    profile = await github_client.fetch_profile("janedoe")

    assert profile["repos"][0]["readme_excerpt"] is None


@respx.mock
async def test_fetch_profile_only_fetches_readmes_for_top_repos(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/janedoe").mock(return_value=httpx.Response(200, json={"public_repos": 6}))
    respx.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"name": f"repo{i}", "full_name": f"janedoe/repo{i}", "stargazers_count": 6 - i}
                    for i in range(6)
                ]
            },
        )
    )
    readme_route = respx.get(url__regex=rf"{BASE_URL}/repos/janedoe/repo\d/readme").mock(
        return_value=httpx.Response(200, text="readme")
    )

    profile = await github_client.fetch_profile("janedoe")

    assert readme_route.call_count == 5  # _README_FETCH_LIMIT
    assert profile["repos"][5]["readme_excerpt"] is None


@respx.mock
async def test_fetch_profile_sends_auth_header_when_token_configured(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token="gh-token")
    profile_route = respx.get(f"{BASE_URL}/users/janedoe").mock(
        return_value=httpx.Response(200, json={"public_repos": 0})
    )
    respx.get(f"{BASE_URL}/search/repositories").mock(return_value=httpx.Response(200, json={"items": []}))

    await github_client.fetch_profile("janedoe")

    assert profile_route.calls[0].request.headers["Authorization"] == "Bearer gh-token"


@respx.mock
async def test_fetch_profile_detects_organization_and_uses_org_search_qualifier(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "Organization",
                "name": "Acme Corp",
                "description": "We build things.",
                "public_repos": 3,
            },
        )
    )
    repos_route = respx.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    profile = await github_client.fetch_profile("acme")

    assert profile["account_type"] == "Organization"
    assert profile["bio"] == "We build things."  # falls back to "description" field
    assert repos_route.calls[0].request.url.params["q"] == "org:acme"


@respx.mock
async def test_fetch_profile_defaults_to_user_search_qualifier(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/janedoe").mock(return_value=httpx.Response(200, json={"type": "User", "public_repos": 1}))
    repos_route = respx.get(f"{BASE_URL}/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    profile = await github_client.fetch_profile("janedoe")

    assert profile["account_type"] == "User"
    assert repos_route.calls[0].request.url.params["q"] == "user:janedoe"


@respx.mock
async def test_fetch_profile_returns_none_on_404(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/ghost").mock(return_value=httpx.Response(404, json={}))

    result = await github_client.fetch_profile("ghost")

    assert result is None


@respx.mock
async def test_fetch_profile_returns_none_on_network_error(patch_settings):
    patch_settings(github_api_base_url=BASE_URL, github_api_token=None)
    respx.get(f"{BASE_URL}/users/janedoe").mock(side_effect=httpx.ConnectError("boom"))

    result = await github_client.fetch_profile("janedoe")

    assert result is None
