import type { TranscriptTurn } from '../types';

const ROLE_LABELS: Record<TranscriptTurn['role'], string> = {
  interviewer: 'Interviewer',
  candidate: 'Candidate',
};

export function buildTranscriptMarkdown(
  summary: string,
  turns: TranscriptTurn[],
  sessionId: string | null,
): string {
  const parts: string[] = ['# Interview Record'];
  if (sessionId) parts.push(`\n_Session: ${sessionId}_`);

  parts.push('\n## Summary\n');
  parts.push(summary?.trim() ? summary.trim() : '_Summary unavailable._');

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
): void {
  const markdown = buildTranscriptMarkdown(summary, turns, sessionId);
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
