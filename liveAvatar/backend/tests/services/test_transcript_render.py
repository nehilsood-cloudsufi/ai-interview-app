from app.models import TranscriptTurn
from app.services.transcript_render import ROLE_LABELS, render_transcript


def test_renders_one_labeled_line_per_turn():
    turns = [
        TranscriptTurn(role="interviewer", text="Tell me about your company."),
        TranscriptTurn(role="candidate", text="We build widgets."),
    ]
    assert render_transcript(turns) == (
        "Interviewer: Tell me about your company.\nCandidate: We build widgets."
    )


def test_skips_empty_and_whitespace_only_turns():
    turns = [
        TranscriptTurn(role="interviewer", text="Hello?"),
        TranscriptTurn(role="candidate", text="   "),
        TranscriptTurn(role="candidate", text=""),
    ]
    assert render_transcript(turns) == "Interviewer: Hello?"


def test_unknown_role_falls_back_to_title_case():
    # "system" turns (profile-correction notes from PATCH /profile) aren't in
    # ROLE_LABELS - they must still render, as "System: ...", not be dropped.
    turns = [TranscriptTurn(role="system", text="Profile corrected: company_name -> Acme")]
    assert render_transcript(turns) == "System: Profile corrected: company_name -> Acme"


def test_empty_transcript_renders_empty_string():
    assert render_transcript([]) == ""


def test_role_labels_cover_the_two_speaking_roles():
    # host_agent formats the latest utterance with ROLE_LABELS["candidate"]
    # directly, so the dict itself is part of the public contract.
    assert ROLE_LABELS == {"interviewer": "Interviewer", "candidate": "Candidate"}
