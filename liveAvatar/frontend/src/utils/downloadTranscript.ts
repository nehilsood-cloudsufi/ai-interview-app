import type { ScorecardData, ScoutFinding, TranscriptTurn } from '../types';

const ROLE_LABELS: Record<TranscriptTurn['role'], string> = {
  interviewer: 'Interviewer',
  candidate: 'Candidate',
};

export interface TranscriptExtras {
  scorecard?: ScorecardData | null;
  insights?: ScoutFinding[] | null;
}

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

  const { scorecard, insights } = extras;

  if (scorecard) {
    parts.push('\n## Scorecard\n');
    for (const category of scorecard.categories) {
      const score = category.score !== null ? `${category.score}/5` : 'not scored';
      parts.push(`- **${category.name}:** ${score} (weight ${Math.round(category.weight * 100)}%)`);
    }
    parts.push(
      scorecard.overall !== null
        ? `\n**Overall:** ${scorecard.overall}/5`
        : '\n**Overall:** not scored',
    );
  }

  if (insights && insights.length > 0) {
    parts.push('\n## Research Insights\n');
    for (const finding of insights) {
      const source = finding.source_url ? ` ([source](${finding.source_url}))` : '';
      parts.push(`- **${finding.topic}:** ${finding.summary}${source}`);
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
