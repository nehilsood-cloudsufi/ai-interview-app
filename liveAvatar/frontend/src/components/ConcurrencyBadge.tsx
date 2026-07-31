import { Users } from 'lucide-react';

/**
 * Small pill showing how many interview sessions are live right now (a people
 * icon + the number). Mounted by InterviewHeader on the right of the top bar,
 * in both avatar and chat modes; the count comes from App's useConcurrencyPoll.
 * Renders nothing until a count is known (count === null).
 */
export function ConcurrencyBadge({ count }: { count: number | null }) {
  if (count === null) return null;
  return (
    <div className="flex items-center gap-2 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50" title="Active Sessions">
        <Users className="w-4 h-4 text-sky-400" />
        <span className="text-xs font-semibold text-slate-300">{count}</span>
    </div>
  );
}
