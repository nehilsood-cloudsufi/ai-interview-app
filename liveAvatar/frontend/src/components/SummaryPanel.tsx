import { X, Download, Loader2, AlertTriangle } from 'lucide-react';
import type { TranscriptTurn } from '../types';
import { downloadTranscript } from '../utils/downloadTranscript';

interface SummaryPanelProps {
  visible: boolean;
  isGenerating: boolean;
  summary: string;
  turns: TranscriptTurn[];
  sessionId: string | null;
  error: string | null;
  onDismiss: () => void;
}

export function SummaryPanel({
  visible,
  isGenerating,
  summary,
  turns,
  sessionId,
  error,
  onDismiss,
}: SummaryPanelProps) {
  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-3xl max-h-[90vh] flex flex-col bg-slate-900 rounded-2xl border border-slate-700/60 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/50 shrink-0">
          <h2 className="text-lg font-bold text-white">Interview Summary</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => downloadTranscript(summary, turns, sessionId)}
              disabled={isGenerating}
              className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed text-slate-200 text-sm font-semibold px-3 py-1.5 rounded-lg border border-slate-700/50 transition-colors"
            >
              <Download className="w-4 h-4" />
              Download
            </button>
            <button
              onClick={onDismiss}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {/* Summary section */}
          <section>
            {isGenerating ? (
              <div className="flex items-center gap-3 text-slate-400">
                <Loader2 className="w-5 h-5 animate-spin" />
                <span className="text-sm">Generating summary…</span>
              </div>
            ) : (
              <>
                {error && (
                  <div className="flex items-start gap-2 mb-4 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                    <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                    <span className="text-sm">{error}</span>
                  </div>
                )}
                {summary ? (
                  <pre className="whitespace-pre-wrap font-sans text-sm text-slate-200 leading-relaxed">{summary}</pre>
                ) : (
                  !error && <p className="text-sm text-slate-500 italic">No summary available.</p>
                )}
              </>
            )}
          </section>

          {/* Full transcript section */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">Full Transcript</h3>
            {turns.length === 0 ? (
              <p className="text-sm text-slate-500 italic">No transcript captured.</p>
            ) : (
              <div className="space-y-3">
                {turns.map((turn, i) => (
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
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
