import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_GITHUB_REQUEST_TIMEOUT_SECONDS = 15.0  # per-request httpx timeout for all GitHub API calls
_USERNAME_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)")  # extracts the username/org path segment from a github.com URL
_README_CHARS = 1500  # max chars kept from each fetched README (payload-size control)
_README_FETCH_LIMIT = 5  # only the top-N starred repos (by stargazers_count) get their README fetched

_NON_PROFILE_PATH_SEGMENTS = {"orgs", "sponsors", "marketplace", "topics", "settings"}  # github.com/<segment> paths that are never a username


def derive_username(url: str) -> str | None:
    """Extracts the GitHub username/org from a github.com profile or repo
    URL. Returns None if the URL doesn't match a profile pattern, or if the
    matched segment is a known non-profile path (e.g. github.com/orgs/...)."""
    match = _USERNAME_RE.search(url)
    if not match:
        return None
    username = match.group(1)
    if username.lower() in _NON_PROFILE_PATH_SEGMENTS:
        return None
    return username


async def _fetch_readme(client: httpx.AsyncClient, headers: dict[str, str], full_name: str) -> str | None:
    """Fetches a repo's raw README text (capped at _README_CHARS). Returns
    None if the repo has no README or the request fails - never raises, so a
    single missing/failed README never aborts the caller's repo loop."""
    try:
        response = await client.get(
            f"{settings.github_api_base_url}/repos/{full_name}/readme",
            headers={**headers, "Accept": "application/vnd.github.raw"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("No README fetched for %s: %s", full_name, exc)
        return None
    return response.text[:_README_CHARS].strip() or None


async def _fetch_starred_repos(client: httpx.AsyncClient, headers: dict[str, str], username: str, is_org: bool) -> list[dict[str, Any]]:
    """Fetches the user/org's top repos by star count via GitHub's Search
    API, with README excerpts for the top _README_FETCH_LIMIT of them.
    Propagates any httpx.HTTPError so fetch_profile's error handling still
    applies to failures here."""
    # Search API sorted by stars surfaces the most significant/representative
    # work, not just recently touched repos.
    qualifier = "org" if is_org else "user"
    repos_response = await client.get(
        f"{settings.github_api_base_url}/search/repositories",
        params={"q": f"{qualifier}:{username}", "sort": "stars", "order": "desc", "per_page": 10},
        headers=headers,
    )
    repos_response.raise_for_status()
    repos = repos_response.json().get("items", [])

    repos_out: list[dict[str, Any]] = []
    for index, repo in enumerate(repos):
        readme = await _fetch_readme(client, headers, repo["full_name"]) if index < _README_FETCH_LIMIT else None
        repos_out.append(
            {
                "name": repo.get("name"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "stargazers_count": repo.get("stargazers_count"),
                "html_url": repo.get("html_url"),
                "readme_excerpt": readme,
            }
        )
    return repos_out


async def fetch_profile(username: str) -> dict[str, Any] | None:
    """Fetches a public GitHub profile plus its most-starred repos (with
    README excerpts, so the findings report can describe what each project
    actually is/does, not just its name) via GitHub's REST + Search APIs.
    Works for both individual users and organizations (`/users/{name}` serves
    both - the response's `type` field distinguishes them, which determines
    whether the repo search uses the `user:`/`org:` qualifier). Returns None
    (logged, not raised) on any failure so the scout pipeline can degrade
    gracefully instead of aborting the whole run."""
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_api_token:
        headers["Authorization"] = f"Bearer {settings.github_api_token}"

    try:
        async with httpx.AsyncClient(timeout=_GITHUB_REQUEST_TIMEOUT_SECONDS) as client:
            profile_response = await client.get(
                f"{settings.github_api_base_url}/users/{username}", headers=headers
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            is_org = profile.get("type") == "Organization"
            repos_out = await _fetch_starred_repos(client, headers, username, is_org)
    except httpx.HTTPError as exc:
        logger.warning("GitHub lookup failed for %s: %s", username, exc)
        return None

    return {
        "username": username,
        "account_type": "Organization" if is_org else "User",
        "name": profile.get("name"),
        # Orgs use "description" instead of "bio" for the same free-text field.
        "bio": profile.get("bio") or profile.get("description"),
        "company": profile.get("company"),
        "location": profile.get("location"),
        "public_repos": profile.get("public_repos"),
        "followers": profile.get("followers"),
        "blog": profile.get("blog"),
        "html_url": profile.get("html_url"),
        "hireable": profile.get("hireable"),
        "created_at": profile.get("created_at"),
        "repos": repos_out,
    }
