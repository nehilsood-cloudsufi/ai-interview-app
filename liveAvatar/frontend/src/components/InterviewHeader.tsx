import { Sparkles } from 'lucide-react';
import { NetworkIndicator } from './NetworkIndicator';
import { ConcurrencyBadge } from './ConcurrencyBadge';
import { formatTime } from '../utils/formatTime';
import type { InterviewMode, NetworkQuality, SessionStatus } from '../types';

interface InterviewHeaderProps {
  mode: InterviewMode;
  status: SessionStatus;
  networkQuality: NetworkQuality;
  concurrencyCount: number | null;
  // Prod tier: seconds left of the picked session length (timer counts DOWN,
  // amber in the final minute). Dev tier: null -> elapsed timer instead.
  remainingSeconds: number | null;
  // Elapsed seconds since the session connected (dev-tier timer display).
  sessionDuration: number;
}

/**
 * The interview room's top bar: product mark + title on the left (with the
 * network indicator in avatar mode), and on the right the active-sessions
 * badge plus - avatar mode only - the session timer pill and the
 * rose/amber/emerald connection-status pill. Extracted verbatim from App.tsx;
 * purely presentational, all state lives in the hooks App wires up.
 */
export function InterviewHeader({
  mode,
  status,
  networkQuality,
  concurrencyCount,
  remainingSeconds,
  sessionDuration,
}: InterviewHeaderProps) {
  return (
    <div className="p-4 md:px-8 md:py-5 flex justify-between items-center shrink-0 border-b border-slate-800/60">
      <div className="flex items-center gap-4 min-w-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-sky-500 flex items-center justify-center shrink-0">
            <Sparkles className="w-4.5 h-4.5 text-white" />
          </div>
          <div className="min-w-0">
            <h1 className="text-base md:text-lg font-bold leading-tight truncate">Vendor Interview</h1>
            <p className="text-xs text-slate-500 leading-tight truncate">
              {mode === 'chat' ? 'Text chat · ' : ''}Hosted by Noor
            </p>
          </div>
        </div>
        {mode === 'avatar' && <NetworkIndicator networkQuality={networkQuality} />}
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <ConcurrencyBadge count={concurrencyCount} />
        {mode === 'avatar' && status === 'connected' && (
          <span
            className={`font-mono px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wider shadow-inner border ${
              remainingSeconds !== null && remainingSeconds <= 60
                ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
            }`}
            title={remainingSeconds !== null ? 'Time remaining' : 'Session time'}
          >
            {remainingSeconds !== null ? `${formatTime(remainingSeconds)} left` : formatTime(sessionDuration)}
          </span>
        )}
        {mode === 'avatar' && (
          <div className="flex items-center gap-2.5 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50">
            <div
              className={`w-2.5 h-2.5 rounded-full shadow-sm ${
                status === 'connected'
                  ? 'bg-emerald-500 shadow-emerald-500/50'
                  : status === 'connecting'
                    ? 'bg-amber-500 animate-pulse shadow-amber-500/50'
                    : 'bg-rose-500 shadow-rose-500/50'
              }`}
            />
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">{status}</span>
          </div>
        )}
      </div>
    </div>
  );
}
