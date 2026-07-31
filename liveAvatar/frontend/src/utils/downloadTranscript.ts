/**
 * Builds and downloads the human-readable interview record as Markdown,
 * including the Data Scout Agent's research report (also rendered live in
 * SummaryPanel — see useInterviewSummary).
 */
import type { FollowupRecommendation, ScorecardData, ScoutReport, TranscriptTurn } from '../types';

const ROLE_LABELS: Record<TranscriptTurn['role'], string> = {
  interviewer: 'Interviewer',
  candidate: 'Candidate',
};

export interface TranscriptExtras {
  scorecard?: ScorecardData | null;
  insights?: ScoutReport | null;
  recommendation?: FollowupRecommendation | null;
}

/**
 * Assembles the full Markdown record: summary, then (when present) scorecard,
 * research insights, and recommendation sections, then the full transcript.
 * Missing/unscored pieces render as explicit placeholders. Returns the string.
 */
export function buildTranscriptMarkdown(
  summary: string,
  turns: TranscriptTurn[],
  sessionId: string | null,
  extras: TranscriptExtras = {},
): string {
  const parts: string[] = ['# Interview Record'];
  if (sessionId) parts.push(`\n_Session: ${sessionId}_`);

  parts.push('\n## Summary\n');
  parts.push(summary?.trim() ? summary.trim() : '_Summary unavailable._');

  const { scorecard, insights, recommendation } = extras;

  if (scorecard) {
    parts.push('\n## Scorecard\n');
    for (const category of scorecard.categories) {
      const value = category.value !== null ? category.value : 'not covered';
      parts.push(`- **${category.name}:** ${value} (weight ${Math.round(category.weight * 100)}%)`);
    }
    const statusStr = scorecard.status ?? 'unscored';
    parts.push(
      scorecard.overall !== null
        ? `\n**Overall:** ${scorecard.overall}/100 (**${statusStr}**)`
        : '\n**Overall:** not scored',
    );
  }

  // Array.isArray: same stale-shape guard as SummaryPanel's ScoutReportSection
  // - a version-mismatched backend still serving the pre-migration findings
  // array would otherwise throw on .interview_claims inside the Download
  // click handler, silently killing the export.
  if (insights && !Array.isArray(insights) && (insights.internet_findings || insights.interview_claims.length > 0)) {
    parts.push('\n## Data Scout Report\n');
    if (insights.company_website) {
      parts.push(`_Company website: ${insights.company_website}_\n`);
    }
    if (insights.interview_claims.length > 0) {
      parts.push('### Claims made in the interview\n');
      for (const claim of insights.interview_claims) {
        parts.push(`- ${claim}`);
      }
      parts.push('');
    }
    if (insights.internet_findings) {
      parts.push(insights.internet_findings);
    } else if (!insights.findings_ok) {
      parts.push('_Research synthesis did not complete for this interview._');
    }
  }

  if (recommendation) {
    parts.push('\n## Recommendation\n');
    parts.push(
      `- **Recommendation:** ${
        recommendation.kind === 'advance'
          ? 'Advance to a follow-up conversation'
          : 'Clarification needed'
      }`,
    );
    parts.push(`- **Reason:** ${recommendation.reason}`);
    if (recommendation.focus_categories.length > 0) {
      parts.push(`- **Focus areas:** ${recommendation.focus_categories.join(', ')}`);
    }
  }

  parts.push('\n## Full Transcript\n');
  if (turns.length === 0) {
    parts.push('_No transcript captured._');
  } else {
    for (const turn of turns) {
      parts.push(`**${ROLE_LABELS[turn.role]}:** ${turn.text}`);
    }
  }

  return parts.join('\n');
}

/**
 * Renders the record via buildTranscriptMarkdown and triggers a browser
 * download of `interview-{sessionId}.md` (a temporary object URL + anchor
 * click, cleaned up afterward).
 */
export function downloadTranscript(
  summary: string,
  turns: TranscriptTurn[],
  sessionId: string | null,
  extras: TranscriptExtras = {},
): void {
  const markdown = buildTranscriptMarkdown(summary, turns, sessionId, extras);
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `interview-${sessionId ?? 'session'}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
