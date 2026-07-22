import { CalendarClock } from 'lucide-react';
import type { FollowupRecommendation, ScorecardData } from '../types';

interface FollowupPanelProps {
  recommendation: FollowupRecommendation | null | undefined;
  scorecard?: ScorecardData | null;
}

/**
 * Compact recommendation card shown inside the summary overlay once the
 * follow-up recommendation arrives from the pipeline. Renders nothing when
 * there is no recommendation.
 *
 * Mounted by SummaryPanel below the scorecard. `recommendation` (from the
 * Coordinator) sets the headline - advance vs clarification-needed - the
 * reason line, and the focus categories; `scorecard` is used only to map those
 * category ids to their human-readable names for the chips.
 */
export function FollowupPanel({ recommendation, scorecard }: FollowupPanelProps) {
  if (!recommendation) return null;

  const nameById = new Map((scorecard?.categories ?? []).map((c) => [c.id, c.name]));

  const headline =
    recommendation.kind === 'advance'
      ? 'Recommend: advance to a follow-up conversation'
      : 'Recommend: clarification needed';

  return (
    <section className="bg-indigo-500/10 border border-indigo-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <CalendarClock className="w-4 h-4 text-indigo-300 shrink-0" />
        <h3 className="text-sm font-bold text-indigo-200">{headline}</h3>
      </div>

      <p className="text-sm text-slate-300 leading-relaxed">{recommendation.reason}</p>

      {recommendation.focus_categories.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {recommendation.focus_categories.map((category) => (
            <span
              key={category}
              className="text-[11px] font-medium px-2 py-0.5 bg-indigo-500/15 border border-indigo-500/25 rounded-full text-indigo-200"
            >
              {nameById.get(category) ?? category}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
