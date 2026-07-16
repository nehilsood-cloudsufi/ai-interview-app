import { useEffect, useRef } from 'react';
import { FileText } from 'lucide-react';
import type { TranscriptTurn } from '../types';

interface TranscriptPanelProps {
  turns: TranscriptTurn[];
}

export function TranscriptPanel({ turns }: TranscriptPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest turn.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  return (
    <div className="w-full md:w-80 lg:w-96 shrink-0 flex flex-col bg-slate-900/60 backdrop-blur-md rounded-2xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50 shrink-0">
        <FileText className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Live Transcript</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {turns.length === 0 ? (
          <p className="text-sm text-slate-500 italic">The conversation transcript will appear here as you speak…</p>
        ) : (
          turns.map((turn, i) => (
            <div key={i} className="flex flex-col gap-1">
              <span
                className={`text-[11px] font-semibold uppercase tracking-wider ${
                  turn.role === 'interviewer' ? 'text-emerald-400' : 'text-sky-400'
                }`}
              >
                {turn.role === 'interviewer' ? 'Interviewer' : 'Candidate'}
              </span>
              <p className="text-sm text-slate-200 leading-relaxed">{turn.text}</p>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
