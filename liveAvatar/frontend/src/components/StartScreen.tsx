import { useState } from 'react';
import { AlertTriangle, ExternalLink, Loader2, MessageSquareText, Sparkles, Video } from 'lucide-react';
import { API_URL, SESSIONS_SHEET_URL } from '../config';
import type { CreateInterviewResponse, InterviewMode } from '../types';

interface StartScreenProps {
  // Creates the interview and hands control to the interview view in the
  // chosen mode. The caller owns the interview_id from here on.
  onStart: (interviewId: string, mode: InterviewMode) => void;
}

export function StartScreen({ onStart }: StartScreenProps) {
  const [pending, setPending] = useState<InterviewMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  const begin = async (mode: InterviewMode) => {
    try {
      setPending(mode);
      setError(null);

      const res = await fetch(`${API_URL}/api/interview`, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to start the interview');

      const data: CreateInterviewResponse = await res.json();
      onStart(data.interview_id, mode);
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
        </div>
        <p className="text-slate-400 text-sm md:text-base max-w-md leading-relaxed">
          A vendor evaluation interview hosted by <span className="text-slate-200 font-medium">Noor</span>.
          Choose how you'd like to take part.
        </p>
      </div>

      {/* Actions */}
      <div className="w-full max-w-md flex flex-col gap-3">
        <button
          onClick={() => begin('avatar')}
          disabled={busy}
          className="w-full flex items-center justify-center gap-3 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-8 py-4 rounded-2xl font-bold transition-all duration-500 shadow-[0_0_40px_-10px_rgba(99,102,241,0.5)] hover:shadow-[0_0_60px_-15px_rgba(99,102,241,0.7)] hover:-translate-y-1 disabled:opacity-50 disabled:hover:translate-y-0 text-lg"
        >
          {pending === 'avatar' ? <Loader2 className="w-6 h-6 animate-spin" /> : <Video className="w-6 h-6" />}
          Start Interview
        </button>

        <button
          onClick={() => begin('chat')}
          disabled={busy}
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
