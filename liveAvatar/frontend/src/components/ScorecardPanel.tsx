import { ClipboardList } from 'lucide-react';
import type { CategoryScoreData, InterviewStateResponse } from '../types';

interface ScorecardPanelProps {
  state: InterviewStateResponse | null;
}

function CategoryRow({ category }: { category: CategoryScoreData }) {
  const pending = category.score === null;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-sm font-medium ${pending ? 'text-slate-500' : 'text-slate-200'}`}>
          {category.name}
        </span>
        <span className="text-[11px] text-slate-500 shrink-0">{Math.round(category.weight * 100)}%</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
          {!pending && (
            <div
              className="h-full rounded-full bg-emerald-500 transition-all duration-500"
              style={{ width: `${(category.score! / 5) * 100}%` }}
            />
          )}
        </div>
        <span
          className={`text-xs font-mono w-12 text-right ${
            pending ? 'text-slate-600 italic' : 'text-slate-200'
          }`}
        >
          {pending ? 'pending' : category.score!.toFixed(1)}
        </span>
      </div>
      {category.evidence.length > 0 && (
        <details>
          <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300 select-none">
            Evidence ({category.evidence.length})
          </summary>
          <ul className="mt-1 space-y-1 pl-3 border-l border-slate-700/50">
            {category.evidence.map((quote, i) => (
              <li key={i} className="text-xs text-slate-400 italic leading-relaxed">
                &ldquo;{quote}&rdquo;
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function ScorecardPanel({ state }: ScorecardPanelProps) {
  const scorecard = state?.scorecard ?? null;
  const overall = scorecard?.overall ?? null;

  return (
    <div className="w-full md:w-80 lg:w-96 shrink-0 flex flex-col bg-slate-900/60 backdrop-blur-md rounded-2xl border border-slate-700/50 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50 shrink-0">
        <ClipboardList className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Live Scorecard</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Overall headline */}
        <div className="flex items-end justify-between gap-2">
          {overall !== null ? (
            <span className="text-3xl font-bold text-emerald-400">
              {overall.toFixed(1)}
              <span className="text-base font-semibold text-slate-400"> / 5</span>
            </span>
          ) : (
            <span className="text-sm text-slate-500 italic">Pending first scores…</span>
          )}
          {scorecard && (
            <span className="text-xs text-slate-400 shrink-0 pb-1">
              {scorecard.answered_questions} answered
            </span>
          )}
        </div>

        {state?.current_topic && (
          <p className="text-xs text-slate-400">
            Current topic: <span className="text-slate-200 font-medium">{state.current_topic}</span>
          </p>
        )}

        {/* Category rows */}
        {!scorecard ? (
          <p className="text-sm text-slate-500 italic">
            Scores will appear here as the interview progresses…
          </p>
        ) : (
          <div className="space-y-3">
            {scorecard.categories.map((category) => (
              <CategoryRow key={category.id} category={category} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
