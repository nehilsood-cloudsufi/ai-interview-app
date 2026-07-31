import { X, Download, Loader2, AlertTriangle, Check, ExternalLink } from 'lucide-react';
import type { FollowupRecommendation, PipelineStatus, ScorecardData, ScoutFinding, TranscriptTurn } from '../types';
import { SESSIONS_SHEET_URL } from '../config';
import { downloadTranscript } from '../utils/downloadTranscript';
import { FollowupPanel } from './FollowupPanel';
import { ScorecardPanel } from './ScorecardPanel';
import { TranscriptBubble } from './TranscriptBubble';

interface SummaryPanelProps {
  visible: boolean;
  isGenerating: boolean;
  summary: string;
  turns: TranscriptTurn[];
  sessionId: string | null;
  error: string | null;
  // Gateway mode: pipeline progress + the values it fills in as it runs. All
  // included in the downloadable Markdown record.
  pipelineStatus?: PipelineStatus | null;
  scorecard?: ScorecardData | null;
  // Plumbed in but DELIBERATELY not rendered in this panel - Scout findings
  // reach the human only through the downloaded Markdown record
  // (utils/downloadTranscript.ts). Do not "fix" this by rendering them here.
  insights?: ScoutFinding[] | null;
  recommendation?: FollowupRecommendation | null;
  // True once the pipeline reports its Evaluator call itself failed (e.g. a
  // too-short transcript) - drives the amber note shown in place of the
  // scorecard so the vendor isn't left staring at nothing unexplained.
  evaluationFailed?: boolean;
  onDismiss: () => void;
}

// Ordered post-interview pipeline steps shown in the strip. "interviewed" is
// the pre-scouting handoff state; it maps to the Scouting step being active.
const STEPS: { key: PipelineStatus; label: string }[] = [
  { key: 'scouting', label: 'Scouting' },
  { key: 'evaluating', label: 'Evaluating' },
  { key: 'ready', label: 'Ready' },
];

function activeIndex(status: PipelineStatus): number {
  switch (status) {
    case 'interviewed':
    case 'scouting':
      return 0;
    case 'evaluating':
      return 1;
    case 'ready':
      return STEPS.length; // all complete
    default:
      return -1;
  }
}

function PipelineStrip({ status }: { status: PipelineStatus }) {
  const failed = status === 'failed';
  const current = activeIndex(status);

  return (
    <section className="space-y-2">
      {!failed && (
        <div className="flex items-center gap-2">
          {STEPS.map((step, i) => {
            const done = i < current;
            const active = i === current;
            return (
              <div key={step.key} className="flex items-center gap-2">
                <div
                  className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full border ${
                    done
                      ? 'text-emerald-300 bg-emerald-500/10 border-emerald-500/25'
                      : active
                        ? 'text-indigo-200 bg-indigo-500/10 border-indigo-500/25'
                        : 'text-slate-500 bg-slate-800/40 border-slate-700/50'
                  }`}
                >
                  {done ? (
                    <Check className="w-3 h-3" />
                  ) : active ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : null}
                  {step.label}
                </div>
                {i < STEPS.length - 1 && <span className="text-slate-600">→</span>}
              </div>
            );
          })}
        </div>
      )}
      {failed && (
        <div className="flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span className="text-sm">Post-interview analysis failed — transcript and summary were saved.</span>
        </div>
      )}
    </section>
  );
}

/**
 * The end-of-interview results overlay (a full-screen modal). App mounts it
 * always and flips `visible` on once finalize begins. It fills in
 * progressively as the post-interview pipeline runs (polled into App by
 * useInterviewSummary): the Gemini summary, a PipelineStrip of the
 * scouting -> evaluating -> ready steps, the ScorecardPanel, an optional
 * FollowupPanel recommendation, and the full transcript. The Download button
 * writes the whole record - including the Scout findings that never render
 * here - to Markdown via downloadTranscript.
 */
export function SummaryPanel({
  visible,
  isGenerating,
  summary,
  turns,
  sessionId,
  error,
  pipelineStatus,
  scorecard,
  insights,
  recommendation,
  evaluationFailed,
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
            {SESSIONS_SHEET_URL && (
              <a
                href={SESSIONS_SHEET_URL}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-semibold px-3 py-1.5 rounded-lg border border-slate-700/50 transition-colors"
              >
                <ExternalLink className="w-4 h-4" />
                All sessions
              </a>
            )}
            <button
              onClick={() => downloadTranscript(summary, turns, sessionId, { scorecard, insights, recommendation })}
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

          {/* Post-interview pipeline progress (gateway mode only) */}
          {!isGenerating && pipelineStatus && <PipelineStrip status={pipelineStatus} />}

          {/* Scoring genuinely failed (e.g. a too-short transcript) - explain
              the missing scorecard instead of silently rendering nothing. */}
          {!isGenerating && pipelineStatus === 'ready' && !scorecard && evaluationFailed && (
            <div className="flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span className="text-sm">
                Scoring was unavailable for this interview — the transcript and summary were still saved.
              </span>
            </div>
          )}

          {/* Final scorecard from the holistic end-of-interview scoring pass */}
          {!isGenerating && <ScorecardPanel scorecard={scorecard} />}

          {/* Follow-up recommendation card (only when one was recommended) */}
          {!isGenerating && <FollowupPanel recommendation={recommendation} scorecard={scorecard} />}

          {/* Full transcript section */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">Full Transcript</h3>
            {turns.length === 0 ? (
              <p className="text-sm text-slate-500 italic">No transcript captured.</p>
            ) : (
              <div className="space-y-3">
                {turns.map((turn, i) => <TranscriptBubble key={i} turn={turn} />)}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
