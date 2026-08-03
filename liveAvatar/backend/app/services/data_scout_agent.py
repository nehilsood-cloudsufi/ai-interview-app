"""Data Scout Agent public entry point: orchestrates gathering
(scout_gather) then synthesizes the final report via Gemini. run_scout is
the only function other modules (app.services.pipeline) call.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.services import gemini_client, representative_verification, scout_gather, scout_utils
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

# Fixed category order for additional_findings - mirrors scout_claims_
# report_prompt/scout_system_prompt's category list exactly. Used both in
# the JSON schema's enum and as the fallback/default for any malformed
# category value from the model.
_ADDITIONAL_FINDING_CATEGORIES = ("Financial", "Leadership", "Legal & Risk", "Partnerships", "Workforce", "Recent News")
_DEFAULT_ADDITIONAL_FINDING_CATEGORY = "Recent News"

# Strict JSON schema for _synthesize_claims_report - the structured
# counterpart to scout_system_prompt's Markdown report, for the frontend's
# DataScoutReport component to render directly (see
# scout_claims_report_prompt in config.py for the full judgment rules this
# mirrors).
_CLAIMS_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["CONTRADICTED", "PARTIAL", "VERIFIED", "NO_DATA"]},
                    "claim": {"type": "string"},
                    "note": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "url": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["name", "url", "text"],
                        },
                    },
                },
                "required": ["status", "claim", "note", "sources"],
            },
        },
        "additional_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                    "category": {"type": "string", "enum": list(_ADDITIONAL_FINDING_CATEGORIES)},
                },
                "required": ["text", "url", "category"],
            },
        },
    },
    "required": ["claims", "additional_findings"],
}

# --- Gemini call tuning ---
# Gemini occasionally returns 5xx (server-side overload) on large/complex
# requests like the synthesis call - these are transient, unlike 4xx (bad
# request/auth/quota), which retrying can't fix. A couple of short-backoff
# retries clears most of them without meaningfully slowing down the common
# case. Claim extraction has no retry loop: it soft-fails to [] on any
# error (see scout_gather._extract_claims), so a single attempt is enough.
_SYNTHESIS_MAX_ATTEMPTS = 3
_SYNTHESIS_RETRY_BACKOFF_SECONDS = 3.0
_SYNTHESIS_TIMEOUT_SECONDS = 180.0  # per-attempt httpx timeout; large payloads take a while to process

_GATHER_TIMEOUT_SECONDS = 45.0  # total wall-clock budget for Pass A + Pass B combined before synthesis proceeds with partial data


async def _post_to_gemini(payload: dict[str, Any]) -> str:
    """Sends a chat/completions payload to Gemini (through the shared
    gemini_client, so the pinned-fallback-model retry applies) with
    retry-on-5xx/timeout (up to _SYNTHESIS_MAX_ATTEMPTS), raising immediately
    on 4xx. Returns the model's reply text; raises the last error if all
    attempts fail."""
    last_error: Exception | None = None
    for attempt in range(1, _SYNTHESIS_MAX_ATTEMPTS + 1):
        try:
            response_body = await gemini_client.chat_completion(
                payload,
                timeout=_SYNTHESIS_TIMEOUT_SECONDS,
                fallback_model=settings.gemini_model_fallback,
            )
            return response_body["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Gemini synthesis HTTP %s on attempt %d/%d: %s",
                exc.response.status_code, attempt, _SYNTHESIS_MAX_ATTEMPTS,
                exc.response.text[:500],
            )
            last_error = exc
            if exc.response.status_code < 500 or attempt == _SYNTHESIS_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Gemini synthesis attempt %d/%d got %s (%s), retrying in %.0fs",
                attempt, _SYNTHESIS_MAX_ATTEMPTS, exc.response.status_code, exc, _SYNTHESIS_RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_SYNTHESIS_RETRY_BACKOFF_SECONDS)
        except httpx.TransportError as exc:
            # Read timeouts AND connect-level failures (ConnectTimeout,
            # ConnectError, RemoteProtocolError...) are equally transient -
            # the old ReadTimeout-only catch let a TCP connect blip skip the
            # retry loop sitting right here.
            last_error = exc
            if attempt == _SYNTHESIS_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Gemini synthesis attempt %d/%d failed in transport (%s: %s), retrying in %.0fs",
                attempt, _SYNTHESIS_MAX_ATTEMPTS, type(exc).__name__, exc, _SYNTHESIS_RETRY_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_SYNTHESIS_RETRY_BACKOFF_SECONDS)

    raise last_error  # type: ignore[misc]


async def _synthesize_internet_findings(company_name: str, company_website: str | None, sources: dict[str, Any]) -> str:
    """Truncates the sources payload, then calls Gemini to synthesize the
    full two-section report (Interview Claims, Additional Findings - see
    scout_system_prompt). Returns the rendered markdown; raises on any
    unrecoverable failure so the caller can soft-fail while preserving the
    already-gathered sources.

    Unlike the prior peer-sections design, this DOES receive
    interview_claims - the claim-focused report format requires mapping each
    claim to what public sources say about it, in neutral (non-verification)
    language; see scout_system_prompt's banned-language list for how that
    stays Scout-appropriate rather than Evaluator-appropriate."""
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot synthesize scout findings.")

    truncated_sources = scout_utils._truncate_sources(sources)
    payload_json = json.dumps(truncated_sources, ensure_ascii=False, indent=2)
    logger.info("Synthesis payload size: sources JSON=%d chars", len(payload_json))

    user_content = (
        f"Company name: {company_name}\n"
        f"Company website: {company_website or 'not provided'}\n\n"
        "Structured data gathered from public web sources and the interview "
        "transcript (omitted keys mean that source wasn't found or wasn't "
        "applicable):\n"
        f"{payload_json}"
    )
    # The relationship-entity check (a client-rubric requirement) is a
    # config-driven prompt suffix, not part of the base prompt - blanking
    # scout_relationship_check_entity removes both the dedicated search
    # (scout_gather._gather_relationship_search) and this instruction.
    system_content = settings.scout_system_prompt
    entity = settings.scout_relationship_check_entity.strip()
    if entity:
        system_content = (
            f"{system_content}\n\n"
            f"{settings.scout_relationship_check_prompt.format(entity=entity)}"
        )
    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
    }
    return await _post_to_gemini(payload)


async def _synthesize_claims_report(company_name: str, company_website: str | None, sources: dict[str, Any]) -> dict[str, Any]:
    """The structured-JSON counterpart to _synthesize_internet_findings - a
    SEPARATE Gemini call (same input, same underlying judgment rules, see
    scout_claims_report_prompt in config.py) that returns strict JSON
    (response_format json_schema, same pattern as
    representative_verification.verify_representative) instead of Markdown,
    for the frontend's DataScoutReport component to render directly without
    parsing Markdown. Kept independent of _synthesize_internet_findings
    (rather than replacing it) so the already-tuned Markdown report used by
    ScoutMarkdown.tsx is never at risk of regressing from this addition.

    Returns {"claims": [...], "additional_findings": [...]} - defaults each
    missing/malformed field rather than raising, then soft-fails to
    {"claims": [], "additional_findings": []} on any error (HTTP failure,
    malformed JSON), same never-crash contract as the rest of this module."""
    try:
        if not settings.gemini_api_key:
            return {"claims": [], "additional_findings": []}

        truncated_sources = scout_utils._truncate_sources(sources)
        user_content = (
            f"Company name: {company_name}\n"
            f"Company website: {company_website or 'not provided'}\n\n"
            "Structured data gathered from public web sources and the interview "
            "transcript (omitted keys mean that source wasn't found or wasn't "
            "applicable):\n"
            f"{json.dumps(truncated_sources, ensure_ascii=False, indent=2)}"
        )
        system_content = settings.scout_claims_report_prompt
        entity = settings.scout_relationship_check_entity.strip()
        if entity:
            system_content = (
                f"{system_content}\n\n"
                f"{settings.scout_claims_relationship_check_prompt.format(entity=entity)}"
            )
        payload = {
            "model": settings.scout_gemini_model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "scout_claims_report", "strict": True, "schema": _CLAIMS_REPORT_SCHEMA},
            },
        }
        response_body = await gemini_client.chat_completion(
            payload, timeout=_SYNTHESIS_TIMEOUT_SECONDS, fallback_model=settings.gemini_model_fallback
        )
        parsed = parse_llm_json(response_body["choices"][0]["message"]["content"])
    except Exception as exc:
        logger.warning("Scout structured claims report failed: %s: %s", type(exc).__name__, exc)
        return {"claims": [], "additional_findings": []}

    claims = []
    for item in parsed.get("claims") or []:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        status = status if status in ("CONTRADICTED", "PARTIAL", "VERIFIED", "NO_DATA") else "NO_DATA"
        claims.append({
            "status": status,
            "claim": str(item.get("claim") or ""),
            "note": str(item.get("note") or ""),
            "sources": [
                {"name": str(src.get("name") or ""), "url": str(src.get("url") or ""), "text": str(src.get("text") or "")}
                for src in (item.get("sources") or [])
                if isinstance(src, dict)
            ],
        })
    additional_findings = []
    for item in parsed.get("additional_findings") or []:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        category = category if category in _ADDITIONAL_FINDING_CATEGORIES else _DEFAULT_ADDITIONAL_FINDING_CATEGORY
        additional_findings.append({
            "text": str(item.get("text") or ""),
            "url": str(item.get("url") or ""),
            "category": category,
        })
    return {"claims": claims, "additional_findings": additional_findings}


# --- structured_findings: deterministic parsing of internet_findings ---
# Purely additive - never affects internet_findings itself, and never makes
# an extra Gemini call. Parses the exact fixed dialect scout_system_prompt
# already produces (see that prompt in config.py for the authoritative
# format this mirrors); any parse failure degrades to structured_findings=
# None rather than raising, since this must never break the existing
# internet_findings/claims_report contract.

_SECTION_HEADING_RE = re.compile(r"^## .*$", re.MULTILINE)
_CLAIM_BLOCK_HEADER_RE = re.compile(r"^`(CONTRADICTED|PARTIAL|VERIFIED|NO DATA)`\s+\*\*(.+?)\*\*\s*$")
_SOURCE_LINE_RE = re.compile(r"^↳\s*(.+?)\s*\(([^)]*)\)\s*—\s*(.*)$")
_FINDING_BULLET_WITH_SOURCE_RE = re.compile(r"^-\s*(.+?)\s*\(\[([^\]]+)\]\(([^)]+)\)\)\s*$")

_NO_CLAIMS_PLACEHOLDER = "No claims required attention."
_NO_FINDINGS_PLACEHOLDER = "No additional findings."


def _split_markdown_sections(markdown: str) -> dict[str, str]:
    """Splits markdown into {heading_text: body} by top-level "## " headings.
    A section whose heading contains "Interview Claims"/"Additional Findings"
    is found by substring match (case-insensitive) elsewhere, so the literal
    emoji prefix on the Additional Findings heading doesn't matter here."""
    headings = list(_SECTION_HEADING_RE.finditer(markdown))
    sections: dict[str, str] = {}
    for i, match in enumerate(headings):
        heading = match.group(0).lstrip("#").strip()
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        sections[heading] = markdown[start:end].strip()
    return sections


def _find_section(sections: dict[str, str], contains: str) -> str | None:
    for heading, body in sections.items():
        if contains.lower() in heading.lower():
            return body
    return None


def _parse_claims_section(body: str) -> list[dict[str, Any]]:
    if not body or body == _NO_CLAIMS_PLACEHOLDER:
        return []
    claims: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header_match = _CLAIM_BLOCK_HEADER_RE.match(lines[0])
        if not header_match:
            # Malformed block - skip it rather than let one bad block break
            # every other claim in the report.
            continue
        label, claim_text = header_match.groups()
        description_lines: list[str] = []
        sources: list[dict[str, str]] = []
        for line in lines[1:]:
            source_match = _SOURCE_LINE_RE.match(line)
            if source_match:
                name, url, note = source_match.groups()
                sources.append({"name": name.strip(), "url": url.strip(), "note": note.strip()})
            else:
                description_lines.append(line)
        claims.append({
            "claim": claim_text.strip(),
            "label": label,
            "description": " ".join(description_lines).strip(),
            "sources": sources,
        })
    return claims


def _parse_additional_findings_section(body: str) -> list[dict[str, Any]]:
    if not body or body == _NO_FINDINGS_PLACEHOLDER:
        return []
    findings: list[dict[str, Any]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        match = _FINDING_BULLET_WITH_SOURCE_RE.match(line)
        if match:
            text, source_name, source_url = match.groups()
            findings.append({"text": text.strip(), "source": {"name": source_name.strip(), "url": source_url.strip()}})
        else:
            # The guaranteed no-source sentence ("No prior {entity}
            # relationship found in public records.") - kept as a real entry
            # (source=None) rather than dropped, so the mandatory
            # relationship-check bullet is never silently lost.
            findings.append({"text": line[1:].strip(), "source": None})
    return findings


def _extract_relationship_finding(findings: list[dict[str, Any]], entity: str) -> str | None:
    if not entity:
        return None
    found_prefix = f"Prior {entity} relationship found:"
    none_sentence = f"No prior {entity} relationship found in public records."
    for finding in findings:
        text = finding["text"]
        if text.startswith(found_prefix):
            return text[len(found_prefix):].strip()
        if text == none_sentence:
            return None
    return None


def _normalize_claim_text(text: str) -> set[str]:
    return {token for token in re.sub(r"[^a-z0-9\s]", "", text.lower()).split() if token}


def _guess_claim_category(claim_text: str, claim_details: list[dict[str, Any]]) -> str:
    """Best-effort only: the rendered markdown never prints "category" (it's
    an input tag on interview_claims, not part of the output format), so this
    cross-references the parsed claim's bold text back to its original
    interview_claims entry via normalized token overlap. Defaults to
    "general" when nothing matches confidently - never raises, never blocks
    the rest of structured_findings from being built."""
    target = _normalize_claim_text(claim_text)
    if not target:
        return "general"
    best_score = 0.0
    best_category = "general"
    for detail in claim_details:
        candidate = _normalize_claim_text(str(detail.get("claim", "")))
        if not candidate:
            continue
        overlap = len(target & candidate) / len(target | candidate)
        if overlap > best_score:
            best_score, best_category = overlap, str(detail.get("category", "general"))
    return best_category if best_score >= 0.5 else "general"


def _build_structured_findings(
    internet_findings: str,
    findings_ok: bool,
    company_website: str | None,
    claim_details: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Deterministically parses internet_findings (already synthesized by
    Gemini) into the JSON shape the frontend/other consumers can use without
    parsing Markdown themselves - no extra Gemini call. Returns None
    immediately when synthesis itself didn't produce usable markdown
    (findings_ok False or blank), and on any unexpected parse failure -
    structured_findings degrading to None must never affect
    internet_findings, which is built and returned entirely separately."""
    if not findings_ok or not internet_findings.strip():
        return None
    try:
        sections = _split_markdown_sections(internet_findings)
        claims_body = _find_section(sections, "Interview Claims")
        findings_body = _find_section(sections, "Additional Findings")
        if claims_body is None or findings_body is None:
            return None

        claims = _parse_claims_section(claims_body)
        for claim in claims:
            claim["category"] = _guess_claim_category(claim["claim"], claim_details)

        additional_findings = _parse_additional_findings_section(findings_body)
        entity = settings.scout_relationship_check_entity.strip()
        aramco_relationship = _extract_relationship_finding(additional_findings, entity)

        return {
            "claims": claims,
            "additional_findings": additional_findings,
            "aramco_relationship": aramco_relationship,
            "company_website": company_website,
            "findings_ok": findings_ok,
        }
    except Exception as exc:
        logger.warning("Structured findings parse failed: %s: %s", type(exc).__name__, exc)
        return None


async def run_scout(
    company_name: str,
    representative_name: str,
    extra_context: str | None,
    documents: list[str] | None,
    transcript: str | None,
    email: str = "",
) -> dict[str, Any]:
    """Orchestrates the two-pass Data Scout Agent pipeline. Returns
    {"internet_findings": str, "interview_claims": list[str],
    "sources": dict, "findings_ok": bool, "company_website": str | None,
    "representative_verification": RepresentativeVerification,
    "claims_report": {"claims": [...], "additional_findings": [...]},
    "structured_findings": {...} | None}.
    claims_report is the structured-JSON counterpart of internet_findings
    (see _synthesize_claims_report) for the frontend's DataScoutReport
    component - an independent Gemini call, never blocks or is blocked by
    internet_findings/representative_verification.

    structured_findings is a SEPARATE, deterministic parse of the already-
    synthesized internet_findings markdown (see _build_structured_findings) -
    no extra Gemini call, purely additive, never affects internet_findings
    itself. None when synthesis failed or parsing hit anything unexpected.

    `email` is the interviewed representative's email address, used by
    Representative Identity Verification (see
    app.services.representative_verification) to cross-check their stated
    company. Not yet on the intake form - the production caller
    (app.services.pipeline) passes whatever VendorProfile.email holds, which
    is "" until a form field exists; verification degrades gracefully to
    web-evidence-only in that case, never crashing.

    There is no dedicated website field on the intake form anymore - Pass A
    derives it itself (see scout_gather._derive_company_website) from any URL
    mentioned in extra_context/documents, falling back to a Tavily search for
    the company's official website.

    KNOWN LIMITATION: the production caller (app.services.pipeline) always
    passes documents=None - raw uploaded-document text is not retained after
    upload (only the <=10-bullet vendor_context summary, which arrives here
    as extra_context), so document claims reach extraction only through that
    lossy summary. Passing raw document text through is a follow-up
    (requires retaining it on InterviewState).

    Pass A (blind gathering) never lets the transcript drive any search or
    fetch decision - website derivation/fetch, industry-neutral blind search,
    a representative-name search, and no-scrape link lookups (the latter
    seeded from extra_context/documents/website text only, never the
    transcript). Pass B (candidate-input enrichment) runs if the transcript,
    extra_context, or any document was provided: it extracts factual claims
    from all three (equally weighted) and runs one targeted search per
    selected claim's own search query, deduplicated against everything Pass A
    already found. Claim extraction (Pass B's first step) has no actual
    dependency on Pass A's results, so scout_gather._gather_all runs it
    CONCURRENTLY with Pass A (measured 2026-08-01: this was previously
    serialized after Pass A for no reason, costing the extraction call's
    full duration off the critical path) - only the subsequent targeted
    search genuinely waits on Pass A's already-fetched-URL set, for dedup.
    Neither pass's SEARCH DECISIONS depend on the other regardless of
    timing - Pass A's blind-search queries are fixed templates independent
    of any claim, and Pass B's claim extraction reads only the transcript/
    context/documents - so this concurrency doesn't reintroduce
    transcript-anchoring bias; it does not mean the two are kept apart at
    synthesis time either, the report's Interview Claims section
    deliberately maps each claim to what Pass A/B found about it, in
    neutral (non-verification) language.

    Gathering (Pass A + Pass B combined) is capped at _GATHER_TIMEOUT_SECONDS
    wall-clock - if it doesn't finish in time, synthesis proceeds with
    whatever was gathered so far rather than blocking the request further.

    Scout gathers and presents company-CLAIM data neutrally - it never
    scores, verifies, flags, or compares those. That is the Evaluator's job
    entirely. The one exception is Representative Identity Verification:
    confirming whether the interviewed representative genuinely works where
    they said is squarely a pre-check Scout's own job (not a judgment about
    the company itself), so it computes its own VERIFIED/NOT_FOUND/NO_DATA
    verdict - driven entirely by web evidence, never by the representative's
    email (see representative_verification's module docstring) - in plain
    Python, never via the LLM, and renders its own section at the top of the
    report.

    Synthesis failure only zeroes out `internet_findings` - sources already
    gathered (and any extracted interview_claims) are always returned, since
    they cost real API/HTTP calls and are useful even without a synthesized
    report. Representative verification runs independently after synthesis
    and is never skipped by a synthesis failure - only by its own errors,
    which it degrades gracefully rather than raising."""
    state = scout_gather._GatherState()
    gather_start = time.monotonic()
    try:
        await asyncio.wait_for(
            scout_gather._gather_all(state, company_name, representative_name, extra_context, documents, transcript),
            timeout=_GATHER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "Scout gathering exceeded %.0fs (%s) - proceeding to synthesis with partial data.",
            _GATHER_TIMEOUT_SECONDS, exc,
        )
    except Exception as exc:
        # Belt to the per-source guards inside scout_gather: whatever still
        # escapes (a URL-parsing edge case, an unexpected shape) must degrade
        # to "synthesize what we have", never discard the sources already
        # gathered - _GatherState's partial-progress contract covers
        # exceptions, not just timeouts.
        logger.warning(
            "Scout gathering failed (%s: %s) - proceeding to synthesis with partial data.",
            type(exc).__name__, exc,
        )
    logger.info("[scout-timing] Pass A + Pass B gather (total): %.2fs", time.monotonic() - gather_start)

    # Only include keys that actually have data - an empty "targeted_search"
    # list must never appear as a biasing empty section.
    sources: dict[str, Any] = {}
    if state.pages:
        sources["pages"] = state.pages
    if state.blind_search:
        sources["blind_search"] = state.blind_search
    if state.representative_search:
        sources["representative_search"] = state.representative_search
    if state.relationship_search:
        sources["relationship_search"] = state.relationship_search
    if state.link_lookups:
        sources["link_lookups"] = state.link_lookups
    if state.targeted_search:
        sources["targeted_search"] = state.targeted_search
    if state.claims:
        # Tagged claim objects, not bare strings: carrying the extraction's
        # own category ("specific"/"general") and source lets the synthesis
        # prompt use them directly instead of re-deriving SPECIFIC vs GENERAL
        # from targeted_search presence - a proxy that broke whenever Pass B
        # search was skipped, capped, or deduped to zero results. The
        # top-level "interview_claims" return stays plain strings (that's
        # the ScoutReport/UI contract).
        sources["interview_claims"] = [
            {"claim": detail["claim"], "category": detail["category"], "source": detail["source"]}
            for detail in state.claim_details
        ] or state.claims

    # Synthesis and representative verification are independent Gemini calls
    # (neither's input depends on the other's output) - run them
    # CONCURRENTLY (measured 2026-08-01: verification, ~7s, was previously
    # serialized strictly after synthesis, ~39s, for no reason - it's now
    # hidden entirely behind synthesis's longer duration). return_exceptions
    # =True: each already has its own internal never-raises contract
    # (verify_representative) or its own try/except below (synthesis), but
    # this is a second line of defense so one piece's unexpected failure
    # can never cancel or lose the other's already-in-flight result.
    async def _run_synthesis() -> tuple[str, bool]:
        start = time.monotonic()
        try:
            findings = await _synthesize_internet_findings(company_name, state.company_website, sources)
            ok = True
        except Exception as exc:
            logger.warning("Scout internet-findings synthesis failed: %s: %s", type(exc).__name__, exc)
            findings, ok = "", False
        logger.info("[scout-timing] synthesis (Gemini): %.2fs", time.monotonic() - start)
        return findings, ok

    async def _run_verification():
        # Timed here, inside its own coroutine, rather than around the
        # asyncio.gather call below - each task's own start/stop keeps this
        # an accurate measure of verification's OWN duration, not however
        # long it happened to sit waiting on the (typically much longer)
        # synthesis call to also finish.
        start = time.monotonic()
        result = await representative_verification.verify_representative(
            representative_name, company_name, email, state.representative_search
        )
        logger.info("[scout-timing] representative verification (total): %.2fs", time.monotonic() - start)
        return result

    async def _run_claims_report() -> dict[str, Any]:
        # Independent of _run_synthesis (see _synthesize_claims_report's
        # docstring) - same input, its own Gemini call, timed separately.
        start = time.monotonic()
        result = await _synthesize_claims_report(company_name, state.company_website, sources)
        logger.info("[scout-timing] structured claims report (Gemini): %.2fs", time.monotonic() - start)
        return result

    synthesis_result, verification_result, claims_report_result = await asyncio.gather(
        _run_synthesis(), _run_verification(), _run_claims_report(), return_exceptions=True
    )

    if isinstance(synthesis_result, BaseException):
        logger.warning("Scout internet-findings synthesis failed unexpectedly: %s: %s", type(synthesis_result).__name__, synthesis_result)
        internet_findings, findings_ok = "", False
    else:
        internet_findings, findings_ok = synthesis_result

    if isinstance(verification_result, BaseException):
        logger.warning("Representative verification failed unexpectedly: %s: %s", type(verification_result).__name__, verification_result)
        verification = representative_verification._no_data(scout_utils._check_email(email), company_name)
    else:
        verification = verification_result

    if isinstance(claims_report_result, BaseException):
        logger.warning("Scout structured claims report failed unexpectedly: %s: %s", type(claims_report_result).__name__, claims_report_result)
        claims_report = {"claims": [], "additional_findings": []}
    else:
        claims_report = claims_report_result

    verification_section = representative_verification.render_representative_verification(
        representative_name, company_name, email, verification
    )
    internet_findings = f"{verification_section}\n\n{internet_findings}" if internet_findings else verification_section

    structured_findings = _build_structured_findings(
        internet_findings, findings_ok, state.company_website, state.claim_details
    )

    return {
        "internet_findings": internet_findings,
        "interview_claims": state.claims,
        "sources": sources,
        "findings_ok": findings_ok,
        "company_website": state.company_website,
        "representative_verification": verification,
        "claims_report": claims_report,
        "structured_findings": structured_findings,
    }
