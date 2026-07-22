import { useEffect, useState } from 'react';
import { AlertTriangle, ExternalLink, KeyRound, Loader2, MessageSquareText, Sparkles, Video } from 'lucide-react';
import { API_URL, SESSIONS_SHEET_URL, TIER } from '../config';
import type { CreateInterviewResponse, DomainInfo, DomainsResponse, InterviewMode } from '../types';

interface StartScreenProps {
  // Creates the interview and hands control to the interview view in the
  // chosen mode. The caller owns the interview_id from here on.
  // durationSeconds is set only on the prod tier (the picked session length),
  // so the interview view can show a countdown instead of an elapsed timer.
  onStart: (interviewId: string, mode: InterviewMode, durationSeconds?: number) => void;
}

/**
 * The app's entry view (App's 'start' view). Lets the vendor pick an interview
 * domain (a dev stand-in for the admin-assigned domain; hidden when
 * GET /api/domains returns nothing) and, on the prod tier, enter the demo
 * passcode and choose a session length. "Start Interview" (avatar) or "Use
 * text chat instead" POSTs /api/interview to mint an interview_id, then calls
 * `onStart` with that id, the chosen mode, and - prod only - the picked
 * duration in seconds so the room can show a countdown.
 */
export function StartScreen({ onStart }: StartScreenProps) {
  const [pending, setPending] = useState<InterviewMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainInfo[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>('');
  const [passcode, setPasscode] = useState<string>('');
  const [durationMinutes, setDurationMinutes] = useState<number>(5);

  // In production an admin assigns the vendor's interview domain; here the
  // vendor picks one. If the fetch fails or returns nothing, hide the select
  // entirely and fall back to the server's default domain (no body on POST).
  useEffect(() => {
    let cancelled = false;
    const loadDomains = async () => {
      try {
        const res = await fetch(`${API_URL}/api/domains`);
        if (!res.ok) return;
        const data: DomainsResponse = await res.json();
        if (cancelled || data.domains.length === 0) return;
        setDomains(data.domains);
        // Preselect the server's default domain (falling back to the first
        // entry if the default isn't in the list for any reason).
        const preselect = data.domains.some((d) => d.id === data.default)
          ? data.default
          : data.domains[0].id;
        setSelectedDomain(preselect);
      } catch (err) {
        console.error('Failed to fetch domains:', err);
      }
    };
    loadDomains();
    return () => {
      cancelled = true;
    };
  }, []);

  const begin = async (mode: InterviewMode) => {
    try {
      setPending(mode);
      setError(null);

      const res = await fetch(`${API_URL}/api/interview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(selectedDomain && { domain: selectedDomain }),
          tier: TIER,
          ...(TIER === 'prod' && { passcode, duration_minutes: durationMinutes }),
        }),
      });
      if (!res.ok) {
        // The prod tier has specific, actionable failures; surface them.
        if (res.status === 403) throw new Error('Invalid passcode');
        if (res.status === 503) {
          const detail = (await res.json().catch(() => null))?.detail;
          throw new Error(detail || 'Production tier is not configured');
        }
        throw new Error('Failed to start the interview');
      }

      const data: CreateInterviewResponse = await res.json();
      onStart(data.interview_id, mode, TIER === 'prod' ? durationMinutes * 60 : undefined);
    } catch (err) {
      console.error('Failed to create interview:', err);
      setError(err instanceof Error ? err.message : 'Failed to start the interview');
      setPending(null);
    }
  };

  const busy = pending !== null;

  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-950/40 via-slate-950 to-black px-4 py-10">
      {/* Brand */}
      <div className="flex flex-col items-center text-center mb-10">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-500 flex items-center justify-center shadow-[0_0_40px_-10px_rgba(99,102,241,0.8)]">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <span className="text-3xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-200 to-slate-400">
            Resonance
          </span>
          {TIER === 'prod' && (
            <span className="ml-1 px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wider bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
              Production
            </span>
          )}
        </div>
        <p className="text-slate-400 text-sm md:text-base max-w-md leading-relaxed">
          A vendor evaluation interview hosted by <span className="text-slate-200 font-medium">Noor</span>.
          Choose how you'd like to take part.
        </p>
      </div>

      {/* Actions */}
      <div className="w-full max-w-md flex flex-col gap-3">
        {domains.length > 0 && (
          <div className="flex flex-col gap-1.5 mb-1">
            <label htmlFor="domain-select" className="text-sm font-semibold text-slate-300">
              Interview domain
            </label>
            <select
              id="domain-select"
              value={selectedDomain}
              onChange={(e) => setSelectedDomain(e.target.value)}
              disabled={busy}
              className="w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50"
            >
              {domains.map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.title}
                </option>
              ))}
            </select>
            <p className="text-xs text-slate-500 leading-relaxed">
              Assigned by an admin in production — selectable here for development.
            </p>
          </div>
        )}

        {TIER === 'prod' && (
          <div className="flex flex-col gap-1.5 mb-1">
            <label htmlFor="passcode-input" className="text-sm font-semibold text-slate-300 flex items-center gap-1.5">
              <KeyRound className="w-4 h-4" />
              Demo passcode
            </label>
            <input
              id="passcode-input"
              type="password"
              value={passcode}
              onChange={(e) => setPasscode(e.target.value)}
              disabled={busy}
              placeholder="Required for production sessions"
              className="w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50"
            />
            <label htmlFor="duration-select" className="text-sm font-semibold text-slate-300 mt-2">
              Session length
            </label>
            <select
              id="duration-select"
              value={durationMinutes}
              onChange={(e) => setDurationMinutes(Number(e.target.value))}
              disabled={busy}
              className="w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50"
            >
              {[3, 5, 7, 10].map((minutes) => (
                <option key={minutes} value={minutes}>
                  {minutes} minutes
                </option>
              ))}
            </select>
            <p className="text-xs text-slate-500 leading-relaxed">
              The session ends automatically after this long. Production sessions use avatar credits.
            </p>
          </div>
        )}

        <button
          onClick={() => begin('avatar')}
          disabled={busy || (TIER === 'prod' && !passcode)}
          className="w-full flex items-center justify-center gap-3 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-8 py-4 rounded-2xl font-bold transition-all duration-500 shadow-[0_0_40px_-10px_rgba(99,102,241,0.5)] hover:shadow-[0_0_60px_-15px_rgba(99,102,241,0.7)] hover:-translate-y-1 disabled:opacity-50 disabled:hover:translate-y-0 text-lg"
        >
          {pending === 'avatar' ? <Loader2 className="w-6 h-6 animate-spin" /> : <Video className="w-6 h-6" />}
          Start Interview
        </button>

        <button
          onClick={() => begin('chat')}
          disabled={busy || (TIER === 'prod' && !passcode)}
          className="w-full flex items-center justify-center gap-2.5 bg-slate-800/60 hover:bg-slate-800 text-slate-200 px-8 py-3.5 rounded-2xl font-semibold border border-slate-700/60 transition-all disabled:opacity-50 text-sm"
        >
          {pending === 'chat' ? <Loader2 className="w-5 h-5 animate-spin" /> : <MessageSquareText className="w-5 h-5" />}
          Use text chat instead
        </button>

        <p className="text-xs text-slate-500 text-center px-4 leading-relaxed">
          Text chat is a low-bandwidth fallback — no camera or microphone needed.
        </p>

        {error && (
          <div className="flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span className="text-sm">{error}</span>
          </div>
        )}
      </div>

      {SESSIONS_SHEET_URL && (
        <a
          href={SESSIONS_SHEET_URL}
          target="_blank"
          rel="noreferrer"
          className="mt-10 flex items-center gap-1.5 text-slate-500 hover:text-slate-300 text-sm font-medium transition-colors"
        >
          <ExternalLink className="w-4 h-4" />
          All sessions
        </a>
      )}
    </div>
  );
}
