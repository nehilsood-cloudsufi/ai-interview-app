"""Representative Identity Verification: confirms whether the interviewed
company representative genuinely works at the STATED company, using public
web evidence already gathered by scout_gather (the representative-name
search from Pass A, re-scoped here to results that actually mention the
stated company - see verify_representative). The LLM (gemini_client, strict
JSON, same pattern as evaluator_agent.score_interview) only reads
pre-extracted evidence and reports what it explicitly shows - it never
decides the verdict. All comparison and the final VERIFIED/NOT_FOUND/NO_DATA
verdict is computed here in plain Python.

CORE RULE (2026-08-01 rewrite - see the module-level comment on
_compute_verdict for the incident this fixes): the verdict is driven ENTIRELY
by whether web evidence ties the person to the stated company. The
representative's email is NEVER part of that decision - anyone can type any
email address, so it is at most a plausibility note shown alongside whatever
verdict the evidence produced (see render_representative_verification /
_render_email_lines), never proof of employment on its own.

Never raises - called from data_scout_agent.run_scout, which must never lose
its report to a verification hiccup.
"""

import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.config import settings
from app.services import gemini_client, scout_utils
from app.services.llm_json import parse_llm_json

logger = logging.getLogger(__name__)

_VERIFICATION_TIMEOUT_SECONDS = 30.0  # single attempt, no retry - mirrors scout_gather._extract_claims

# Anti-hallucination is the load-bearing instruction here: the LLM sees only
# evidence ALREADY re-scoped to results that mention the stated company (see
# verify_representative) and must say so ('unknown'/'not found'/empty list)
# for anything not explicitly present, rather than inferring from general
# knowledge about the company or the role. The model reports what the
# evidence shows; whether that adds up to VERIFIED is decided afterward, in
# Python (see verify_representative) - never by the LLM itself, and never by
# the representative's email, which this prompt is never even told about.
_VERIFICATION_SYSTEM_PROMPT = (
    "You verify whether a company representative's public web presence "
    "ties them to the STATED company, using ONLY the numbered evidence "
    "listed below (titles/snippets from public web search results, already "
    "filtered to results that mention the stated company). You do not "
    "decide the final verdict - that happens elsewhere, in code; your job "
    "is only to read the evidence and report what it explicitly shows.\n\n"
    "CRITICAL: set \"tied_to_stated_company\" to true ONLY if the evidence "
    "EXPLICITLY shows this specific person working at, employed by, or "
    "otherwise affiliated with the stated company (e.g. a bio, profile, or "
    "article naming them in that role there) - not merely because the "
    "company name happens to appear somewhere in the same result. If the "
    "evidence does not explicitly make that connection, set it to false and "
    "leave the role/background fields at their placeholder values below, "
    "and return an empty \"citations\" list - never guess or infer from "
    "general knowledge about the company or a common role there. Do NOT "
    "report any OTHER company this person might work at - you are only "
    "ever asked about the STATED company; if they are not tied to it, that "
    "is the entire answer.\n\n"
    "If tied_to_stated_company is true, also report: their role (only if "
    "explicitly named), tenure/background, named projects, and focus areas "
    "- ONLY facts explicitly stated in the evidence.\n\n"
    "Additionally, for each numbered evidence item that materially supports "
    "the tie, add one entry to \"citations\": {\"source_index\": <the "
    "item's number>, \"supports\": \"<a short phrase, under 12 words, "
    "stating exactly what THAT source confirms - e.g. 'confirms current "
    "SVP role at IBM' - based ONLY on what that item's own title/snippet "
    "actually says>\"}. Omit any evidence item that doesn't add anything "
    "new; never cite an item whose content doesn't genuinely support the "
    "connection. Return an empty \"citations\" list when "
    "tied_to_stated_company is false.\n\n"
    'Respond with a single JSON object of exactly this shape: '
    '{"tied_to_stated_company": true|false, "role": "<role per the '
    'evidence, or \'unknown\'>", "background": {"tenure": "<time/positions '
    'at the company per the evidence, or \'not found\'>", "projects": '
    '["<projects explicitly named in the evidence>"], "focus_areas": '
    '["<domains/technologies explicitly named in the evidence>"]}, '
    '"summary": "<one or two neutral sentences stating what was checked '
    'and the outcome>", "citations": [{"source_index": <int>, "supports": '
    '"<short phrase>"}]}'
)

_VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "tied_to_stated_company": {"type": "boolean"},
        "role": {"type": "string"},
        "background": {
            "type": "object",
            "properties": {
                "tenure": {"type": "string"},
                "projects": {"type": "array", "items": {"type": "string"}},
                "focus_areas": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tenure", "projects", "focus_areas"],
        },
        "summary": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_index": {"type": "integer"},
                    "supports": {"type": "string"},
                },
                "required": ["source_index", "supports"],
            },
        },
    },
    "required": ["tied_to_stated_company", "role", "background", "summary", "citations"],
}


@dataclass
class RepresentativeVerification:
    """One VERIFIED/NOT_FOUND/NO_DATA read on the interviewed
    representative. The verdict is driven ENTIRELY by web evidence tying the
    person to the stated company (see verify_representative's
    `tied_to_stated_company` read) - email plays NO role in it.
    `email_domain_plausible` is a separate, purely mechanical string-match
    signal (does the stated company's name appear as a token in the email's
    domain?) used only for the honest plausibility note in the rendered
    output (see render_representative_verification) - never treated as
    proof of anything. `evidence` is populated only for VERIFIED - each item
    is `{"url": str, "source_name": str, "supports": str}`, one per evidence
    item the LLM cited as materially supporting the tie (see the
    "citations" extraction field). No confidence/same-name-collision
    handling is modeled here - deliberately removed 2026-08-02 per product
    decision (a reviewer reads the Evidence block itself to judge strength,
    rather than the system pre-labelling it)."""

    verdict: Literal["VERIFIED", "NOT_FOUND", "NO_DATA"]
    email_valid: bool
    email_type: Literal["corporate", "personal", "invalid", "missing"]
    email_domain: str
    email_domain_plausible: bool
    role: str
    summary: str
    background: dict[str, Any]  # {"tenure": str, "projects": list[str], "focus_areas": list[str]}
    evidence: list[dict[str, str]]  # [{"url": str, "source_name": str, "supports": str}, ...]


def _email_domain_plausible(email_domain: str, company_name: str) -> bool:
    """Pure string check - NO web evidence, NOT proof of anything: does the
    email's domain contain a normalized token of the stated company's name
    (e.g. "ibm.com" for "IBM", "persistent.com" for "Persistent Systems")?
    True domain authenticity (WHOIS/DNS ownership, mail-server
    deliverability) is out of scope for this POC - this only feeds the
    honest plausibility note in the rendered email lines
    (render_representative_verification / _render_email_lines). Deliberately
    never consulted by the verdict logic below - see the module docstring
    for why an email can never raise a verdict."""
    if not email_domain or not company_name:
        return False
    company_tokens = scout_utils._normalize_company_tokens(company_name)
    return any(token in email_domain for token in company_tokens)


def _has_real_evidence(role: str, background: dict[str, Any]) -> bool:
    """True if at least one concrete fact was actually extracted (a named
    role, a tenure/background note, a project, or a focus area) - used both
    to decide VERIFIED vs NOT_FOUND and, defensively, by
    _guard_verified_requires_evidence below. A VERIFIED verdict with none of
    these is a logical contradiction (evidence "ties" the person to the
    company but has nothing to show for it) and must never be produced -
    see the 2026-08-01 incident in the module docstring."""
    return (
        not scout_utils._is_unknown(role)
        or background.get("tenure", "not found") != "not found"
        or bool(background.get("projects"))
        or bool(background.get("focus_areas"))
    )


def _guard_verified_requires_evidence(verification: RepresentativeVerification) -> RepresentativeVerification:
    """Final safety net for the exact bug this module was rewritten to fix
    (2026-08-01: a fabricated person with a hand-typed matching-domain email
    was rendered `VERIFIED` with "Background: not found in public records" -
    a contradiction). A runtime check rather than a bare `assert` (which can
    be stripped under `-O`, and must never be allowed to raise here anyway -
    this module's whole contract is that it never crashes the pipeline): if
    a VERIFIED verdict is ever about to be returned with no real extracted
    fact behind it, this logs loudly and downgrades to NOT_FOUND instead of
    letting the contradiction reach a report a reviewer relies on. Does not
    depend on confidence (removed entirely - see the dataclass docstring) -
    only on whether any real role/background/project/focus_area exists."""
    if verification.verdict != "VERIFIED":
        return verification
    if _has_real_evidence(verification.role, verification.background):
        return verification
    logger.error(
        "Representative verification guard triggered: a VERIFIED verdict had no "
        "supporting role/background/projects/focus_areas - downgrading to NOT_FOUND. "
        "This indicates a model-consistency bug upstream and should be investigated."
    )
    return dataclasses.replace(
        verification,
        verdict="NOT_FOUND",
        role="unknown",
        background={"tenure": "not found", "projects": [], "focus_areas": []},
        evidence=[],
        summary=verification.summary or "No public records were found associating this person with the stated company.",
    )


def _clean_evidence(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Strips a search-result list down to exactly {title, snippet, url} -
    never hands the LLM a raw Tavily/enriched result (which also carries
    source_tier, full page "text", etc.)."""
    return [{"title": r.get("title") or "", "snippet": r.get("snippet") or "", "url": r.get("url") or ""} for r in results]


def _render_evidence_block(person_evidence: list[dict[str, str]]) -> str:
    if not person_evidence:
        return "(no evidence found)"
    return "\n".join(f"{i}. {e['title']} — {e['snippet']} ({e['url']})" for i, e in enumerate(person_evidence, start=1))


def _base_verification(
    verdict: str, email_check: dict[str, Any], company_name: str, summary: str
) -> RepresentativeVerification:
    return RepresentativeVerification(
        verdict=verdict,
        email_valid=email_check["email_valid"],
        email_type=email_check["email_type"],
        email_domain=email_check["email_domain"],
        email_domain_plausible=_email_domain_plausible(email_check["email_domain"], company_name),
        role="unknown",
        summary=summary,
        background={"tenure": "not found", "projects": [], "focus_areas": []},
        evidence=[],
    )


def _no_data(email_check: dict[str, Any], company_name: str = "", note: str | None = None) -> RepresentativeVerification:
    """NO_DATA: verification could not RUN at all (blank name, or the
    search/LLM step failed/was unavailable) - a technical failure, distinct
    from NOT_FOUND (which means verification DID run and found no tie)."""
    return _base_verification(
        "NO_DATA", email_check, company_name,
        note or "Verification could not run (no representative name provided, or the identity search was unavailable).",
    )


def _not_found(email_check: dict[str, Any], company_name: str) -> RepresentativeVerification:
    """NOT_FOUND: verification ran, but no web evidence ties this person to
    the stated company - this is the correct, neutral outcome for a
    mismatched or fabricated identity (see the module docstring's incident).
    Never names any other employer, never speculates."""
    return _base_verification(
        "NOT_FOUND", email_check, company_name,
        f"No public records associate this person with {company_name}. Completed check - not a search failure.",
    )


async def verify_representative(
    representative_name: str, company_name: str, email: str, person_search_entries: list[dict[str, Any]]
) -> RepresentativeVerification:
    """Top-level entry point, called from data_scout_agent.run_scout
    alongside (concurrently with) _synthesize_internet_findings.
    `person_search_entries` is state.representative_search - already
    gathered by scout_gather during Pass A (a "{representative_name}
    {company_name}" search - already company-scoped in its own query, see
    scout_gather._gather_representative_search) - reused here rather than
    re-searching. Never raises."""
    email_check = scout_utils._check_email(email)
    if not representative_name or not representative_name.strip():
        return _no_data(email_check, company_name)

    raw_results = [result for entry in person_search_entries for result in entry.get("results", [])]
    # Company-scoped evidence (requirement: verify WITHIN the stated company
    # only): _gather_representative_search's own relevance filter ORs in the
    # representative's own name alongside company_name (so a legitimate
    # profile that never repeats the company name still survives) - which
    # means a hit matching ONLY the person's name, with no company mention
    # at all, can still be sitting in person_search_entries. That is exactly
    # the gap that let an unrelated bio get treated as "evidence" for a
    # fabricated employee before this rewrite. Re-filtering here with
    # company_name alone (no extra_name) keeps only results that actually
    # mention the STATED company - anything else is ignored, even if the
    # person's name matches exactly.
    company_scoped_results = scout_utils._filter_relevant(raw_results, company_name)
    person_evidence = _clean_evidence(company_scoped_results)

    if not person_evidence:
        # Nothing to verify against - short-circuits before spending an LLM
        # call on evidence already known to be empty. This is the correct,
        # neutral outcome (NOT_FOUND), not a technical failure.
        return _not_found(email_check, company_name)

    if not settings.gemini_api_key:
        return _no_data(email_check, company_name, "Verification could not be completed (no LLM configured).")

    payload = {
        "model": settings.scout_gemini_model,
        "messages": [
            {"role": "system", "content": _VERIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Representative: {representative_name}\nStated company: {company_name}\n\n"
                    f"Evidence:\n{_render_evidence_block(person_evidence)}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "representative_verification", "strict": True, "schema": _VERIFICATION_SCHEMA},
        },
    }
    try:
        with scout_utils._timed("representative verification: extraction (Gemini)"):
            response_body = await gemini_client.chat_completion(
                payload, timeout=_VERIFICATION_TIMEOUT_SECONDS, fallback_model=settings.gemini_model_fallback
            )
        parsed = parse_llm_json(response_body["choices"][0]["message"]["content"])
    except Exception as exc:
        logger.warning("Representative verification extraction failed: %s: %s", type(exc).__name__, exc)
        return _no_data(email_check, company_name, "Verification could not be completed due to an internal error.")

    tied_to_stated_company = bool(parsed.get("tied_to_stated_company"))
    background_raw = parsed.get("background") or {}
    background = {
        "tenure": str(background_raw.get("tenure") or "not found"),
        "projects": [str(p) for p in (background_raw.get("projects") or [])],
        "focus_areas": [str(f) for f in (background_raw.get("focus_areas") or [])],
    }
    role = str(parsed.get("role") or "unknown")
    summary = str(parsed.get("summary") or "")

    if not tied_to_stated_company:
        return _not_found(email_check, company_name)

    # Map each cited source_index back to its evidence item (1-based, same
    # numbering _render_evidence_block gave the LLM) - an out-of-range or
    # malformed index is skipped rather than raising (a model slip here must
    # never crash verification; it just loses one citation line).
    evidence: list[dict[str, str]] = []
    for citation in parsed.get("citations") or []:
        try:
            index = int(citation.get("source_index"))
            entry = person_evidence[index - 1]
        except (TypeError, ValueError, IndexError):
            continue
        supports = str(citation.get("supports") or "").strip()
        if not entry["url"] or not supports:
            continue
        evidence.append({"url": entry["url"], "source_name": _friendly_source_name(entry["url"]), "supports": supports})

    verification = RepresentativeVerification(
        verdict="VERIFIED",
        email_valid=email_check["email_valid"],
        email_type=email_check["email_type"],
        email_domain=email_check["email_domain"],
        email_domain_plausible=_email_domain_plausible(email_check["email_domain"], company_name),
        role=role,
        summary=summary,
        background=background,
        evidence=evidence,
    )
    # See _guard_verified_requires_evidence: the model said "tied" but if it
    # named no actual fact, that is the exact contradiction this rewrite
    # exists to prevent - downgrade rather than render an empty VERIFIED.
    return _guard_verified_requires_evidence(verification)


_FRIENDLY_SOURCE_NAMES = {"linkedin.com": "LinkedIn", "twitter.com": "Twitter/X", "x.com": "Twitter/X"}


def _friendly_source_name(url: str) -> str:
    domain = scout_utils._domain_of(url)
    return _FRIENDLY_SOURCE_NAMES.get(domain, domain)


def _display_url(url: str) -> str:
    """Strips the scheme (and a leading "www.") for a compact display form -
    e.g. "https://www.linkedin.com/in/x" -> "linkedin.com/in/x". Cosmetic
    only - ScoutMarkdown only auto-links `[text](url)` references, not bare
    text, so this is never a functional link either way."""
    stripped = re.sub(r"^https?://", "", url)
    return stripped[4:] if stripped.startswith("www.") else stripped


def _status_line(verdict: str) -> str:
    """Symbol + verdict only - no confidence (removed 2026-08-02)."""
    if verdict == "VERIFIED":
        return "✓ VERIFIED"
    if verdict == "NOT_FOUND":
        return "✗ NOT FOUND"
    return "— NO DATA"


def _render_email_lines(email: str, company_name: str, verification: RepresentativeVerification) -> list[str]:
    """Renders the email as an honest plausibility note, NEVER as proof of
    anything (see the module docstring) - the verdict above is already
    decided from web evidence alone; this only adds what the email format/
    domain itself can honestly say. WHOIS/DNS ownership and mail
    deliverability are out of scope - "plausible" is as far as this goes.
    Returns [] when no email was provided (the intake form has no field for
    it yet - this must degrade gracefully, not render an empty block)."""
    if verification.email_type == "missing":
        return []
    header = f"**Email:** {email}"
    if verification.email_type == "invalid":
        return [header, "Invalid email format."]
    if verification.email_type == "personal":
        if verification.verdict == "VERIFIED":
            return [header, "Personal address — not used for verification. Identity confirmed via public sources instead."]
        return [header, "Personal address — not used for verification."]
    # corporate
    if verification.verdict == "NO_DATA":
        return [header, "Corporate domain; verification could not be completed."]
    if verification.email_domain_plausible:
        if verification.verdict == "VERIFIED":
            return [header, f"Uses {company_name}'s corporate domain (consistent with the verified identity)."]
        # NOT_FOUND with a plausible domain - the exact case this rewrite
        # targets (a hand-typed matching email with no corroborating
        # evidence): the domain match must not be allowed to read as
        # corroboration here.
        return [
            header,
            f"Uses {company_name}'s domain, but no public evidence links this person to {company_name}. "
            "Domain match is not proof.",
        ]
    return [header, f"Corporate domain, but does not obviously match {company_name}'s name."]


def _join_blocks(blocks: list[list[str]]) -> str:
    return "## Representative Verification\n\n" + "\n\n".join("\n".join(block) for block in blocks)


def render_representative_verification(
    representative_name: str, company_name: str, email: str, verification: RepresentativeVerification
) -> str:
    """Renders the '## Representative Verification' block, prepended to
    internet_findings by run_scout - this is the only rendering surface;
    there is no separate frontend change for the structured field. A
    label-driven layout (bold field labels, not colored pill badges) so a
    reviewer needs zero extra work: Status/Name/Role/Company, Summary,
    Focus areas/Known work (omitted when empty - never a placeholder),
    Email (an honest plausibility note, never proof), and Evidence
    (VERIFIED only - each source paired with a short phrase saying what it
    confirms, never just a bare link). No confidence anywhere and no
    "Reviewer action" line (both deliberately removed 2026-08-02) - a
    reviewer reads the Status/Summary/Evidence directly rather than being
    told what to do next."""
    if not representative_name or not representative_name.strip():
        blocks = [
            ["**Status:** — NO DATA", "**Name:** (blank / not provided)"],
            [
                "**Summary:** Verification could not run (no representative name provided, or the identity "
                "search was unavailable)."
            ],
        ]
        return _join_blocks(blocks)

    header_lines = [f"**Status:** {_status_line(verification.verdict)}", f"**Name:** {representative_name}"]
    if verification.verdict == "VERIFIED":
        if not scout_utils._is_unknown(verification.role):
            header_lines.append(f"**Role:** {verification.role}")
        header_lines.append(f"**Company:** {company_name}")
    else:
        header_lines.append(f"**Company:** {company_name} (claimed)")

    blocks = [header_lines, [f"**Summary:** {verification.summary}"]]

    if verification.verdict == "VERIFIED":
        focus_areas = verification.background.get("focus_areas") or []
        projects = verification.background.get("projects") or []
        detail_lines = []
        if focus_areas:
            detail_lines.append(f"**Focus areas:** {' · '.join(focus_areas)}")
        if projects:
            detail_lines.append(f"**Known work:** {' · '.join(projects)}")
        if detail_lines:
            blocks.append(detail_lines)

    email_lines = _render_email_lines(email, company_name, verification)
    if email_lines:
        blocks.append(email_lines)

    if verification.verdict == "VERIFIED" and verification.evidence:
        evidence_lines = ["**Evidence:**"]
        for item in verification.evidence:
            evidence_lines.append(f"- **{item['source_name']}** — {item['supports']}")
            evidence_lines.append(f"  ↳ {_display_url(item['url'])}")
        blocks.append(evidence_lines)

    return _join_blocks(blocks)
