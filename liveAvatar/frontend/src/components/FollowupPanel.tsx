import { useState } from 'react';
import { CalendarClock, Check, Copy, Mail } from 'lucide-react';
import type { FollowupProposal } from '../types';

interface FollowupPanelProps {
  followup: FollowupProposal | null | undefined;
}

// Card shown inside the summary overlay when the Coordinator recommended a
// follow-up meeting. Renders nothing when there is no recommendation.
export function FollowupPanel({ followup }: FollowupPanelProps) {
  const [copied, setCopied] = useState(false);

  if (!followup) return null;

  const headline =
    followup.recommendation.kind === 'advance'
      ? 'Follow-up recommended — deep dive'
      : 'Follow-up recommended — clarification';

  const mailtoHref = `mailto:?subject=${encodeURIComponent(followup.title)}&body=${encodeURIComponent(followup.email_draft)}`;

  const copyEmailDraft = async () => {
    try {
      await navigator.clipboard.writeText(followup.email_draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Could not copy the email draft:', err);
    }
  };

  return (
    <section className="bg-indigo-500/10 border border-indigo-500/20 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        <CalendarClock className="w-4 h-4 text-indigo-300 shrink-0" />
        <h3 className="text-sm font-bold text-indigo-200">{headline}</h3>
      </div>

      <p className="text-sm text-slate-300 leading-relaxed mb-3">{followup.recommendation.reason}</p>

      {followup.agenda.length > 0 && (
        <div className="mb-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-1.5">Suggested agenda</h4>
          <ul className="space-y-1">
            {followup.agenda.map((item, i) => (
              <li key={i} className="text-sm text-slate-200 leading-relaxed flex gap-2">
                <span className="text-indigo-400 shrink-0">•</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-slate-400 mb-4">
        Suggested duration: <span className="text-slate-200 font-semibold">{followup.duration_minutes} minutes</span>
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={copyEmailDraft}
          className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-semibold px-3 py-1.5 rounded-lg border border-slate-700/50 transition-colors"
        >
          {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
          {copied ? 'Copied' : 'Copy email draft'}
        </button>
        <a
          href={mailtoHref}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold px-3 py-1.5 rounded-lg transition-colors"
        >
          <Mail className="w-4 h-4" />
          Open in email
        </a>
      </div>
    </section>
  );
}
