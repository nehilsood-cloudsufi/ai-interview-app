import { Users } from 'lucide-react';

export function ConcurrencyBadge({ count }: { count: number | null }) {
  if (count === null) return null;
  return (
    <div className="flex items-center gap-2 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50" title="Active Sessions">
        <Users className="w-4 h-4 text-sky-400" />
        <span className="text-xs font-semibold text-slate-300">{count}</span>
    </div>
  );
}
