import { useEffect, useRef } from 'react';
import { FileText } from 'lucide-react';
import type { TranscriptTurn } from '../types';
import { TranscriptBubble } from './TranscriptBubble';

interface TranscriptPanelProps {
  turns: TranscriptTurn[];
}

/**
 * The live scrolling transcript rail shown beside the video in avatar mode,
 * one TranscriptBubble per turn and auto-scrolling to the newest. App mounts it
 * in the right-hand column once the session is connected, fed by the turns
 * useLiveAvatarSession captures from the SDK. Shows a placeholder line until
 * the first turn arrives.
 */
export function TranscriptPanel({ turns }: TranscriptPanelProps) {
  const listRef = useRef<HTMLDivElement>(null);

  // Auto-scroll the LIST (not the page) to the newest turn. scrollIntoView
  // would also scroll every scrollable ancestor, which nudged the whole
  // layout as turns accumulated.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  return (
    // min-h-0/max-h-full: without them this flex item's min-height:auto lets
    // the growing turn list stretch the panel (and the whole video row) past
    // the viewport instead of scrolling — the "UI keeps growing" bug seen
    // live on 2026-07-20. max-h-64 caps it on mobile, where the column
    // layout otherwise pushes the controls off-screen.
    <div className="w-full md:w-80 lg:w-96 shrink-0 flex flex-col min-h-0 max-h-64 md:max-h-full bg-slate-900/60 backdrop-blur-md rounded-2xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50 shrink-0">
        <FileText className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Live Transcript</span>
      </div>

      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {turns.length === 0 ? (
          <p className="text-sm text-slate-500 italic">The conversation transcript will appear here as you speak…</p>
        ) : (
          turns.map((turn, i) => <TranscriptBubble key={i} turn={turn} />)
        )}
      </div>
    </div>
  );
}
