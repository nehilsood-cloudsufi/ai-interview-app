import { ClipboardList } from 'lucide-react';
import type { CategoryScoreData, ScorecardData } from '../types';

// Renders the FINAL scorecard inside the results view (SummaryPanel), fed by
// the finalize response. Scoring is one holistic pass at the end of the
// interview - deliberately never shown live, so the vendor's answers aren't
// influenced by watching their own scores move.
interface ScorecardPanelProps {
  scorecard?: ScorecardData | null;
}

function CategoryRow({ category }: { category: CategoryScoreData }) {
  const unscored = category.value === null;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-sm font-medium ${unscored ? 'text-slate-500' : 'text-slate-200'}`}>
          {category.name}
        </span>
        <span className="text-[11px] text-slate-500 shrink-0">{Math.round(category.weight * 100)}%</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
          {!unscored && (
            <div
              className="h-full rounded-full bg-emerald-500"
              style={{ width: `${category.points}%` }}
            />
          )}
        </div>
        {unscored ? (
          <span className="text-xs text-slate-600 italic w-20 text-right">not covered</span>
        ) : (
          <span className="text-xs font-medium px-2 py-0.5 bg-emerald-500/15 border border-emerald-500/25 rounded-full text-emerald-200 w-20 text-center">
            {category.value}
          </span>
        )}
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

export function ScorecardPanel({ scorecard }: ScorecardPanelProps) {
  if (!scorecard) return null;

  return (
    <section className="rounded-xl border border-slate-700/50 bg-slate-800/40 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/50">
        <ClipboardList className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Scorecard</span>
      </div>

      <div className="p-4 space-y-4">
        {/* Overall headline */}
        {scorecard.overall !== null ? (
          <div className="flex items-center gap-3">
            <span className="text-3xl font-bold text-emerald-400">
              {scorecard.overall}
              <span className="text-base font-semibold text-slate-400"> / 100</span>
            </span>
            {scorecard.status && (
              <span
                className={`text-xs font-semibold px-2 py-1 rounded-full ${
                  scorecard.status === 'APPROVED'
                    ? 'bg-emerald-500/15 border border-emerald-500/25 text-emerald-200'
                    : 'bg-rose-500/15 border border-rose-500/25 text-rose-200'
                }`}
              >
                {scorecard.status}
              </span>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500 italic">No categories could be scored from this interview.</p>
        )}

        {/* Category rows */}
        <div className="space-y-3">
          {scorecard.categories.map((category) => (
            <CategoryRow key={category.id} category={category} />
          ))}
        </div>
      </div>
    </section>
  );
}
