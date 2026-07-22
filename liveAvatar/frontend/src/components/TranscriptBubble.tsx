import type { TranscriptTurn } from '../types';

interface TranscriptBubbleProps {
  turn: TranscriptTurn;
}

/**
 * One transcript turn: role label + text, colored by speaker (emerald
 * interviewer / sky candidate). Shared by TranscriptPanel (live view) and
 * SummaryPanel (post-interview view), which rendered this markup identically.
 * ChatPanel deliberately does NOT use it - chat renders aligned message
 * bubbles with "Noor"/"You" labels, a different design.
 *
 * Note: the label is a binary ternary, so non-candidate roles (e.g. the
 * "system" profile-correction notes) render with the Candidate label/color -
 * long-standing display behavior, kept as-is by the extraction.
 */
export function TranscriptBubble({ turn }: TranscriptBubbleProps) {
  return (
    <div className="flex flex-col gap-1">
      <span
        className={`text-[11px] font-semibold uppercase tracking-wider ${
          turn.role === 'interviewer' ? 'text-emerald-400' : 'text-sky-400'
        }`}
      >
        {turn.role === 'interviewer' ? 'Interviewer' : 'Candidate'}
      </span>
      <p className="text-sm text-slate-200 leading-relaxed">{turn.text}</p>
    </div>
  );
}
