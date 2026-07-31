import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Loader2, Send, Square } from 'lucide-react';
import type { TranscriptTurn } from '../types';

interface ChatPanelProps {
  turns: TranscriptTurn[];
  isSending: boolean;
  done: boolean;
  onSend: (text: string) => void;
  onEnd: () => void;
}

/**
 * claude.ai-style text-chat column: scrolling message bubbles, a composer with
 * Enter-to-send / Shift+Enter-for-newline, and an End interview button.
 *
 * The low-bandwidth alternative to the avatar. App mounts it (in place of the
 * video area) whenever mode is 'chat' - either chosen on the StartScreen or
 * after the one-way switch from a weak avatar session. `turns` are the Host
 * conversation so far, `onSend` posts the vendor's next reply, `isSending`
 * drives Noor's typing indicator, and once `done` the composer locks and
 * `onEnd` finalizes the interview.
 */
export function ChatPanel({ turns, isSending, done, onSend, onEnd }: ChatPanelProps) {
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  // Auto-scroll the LIST (not the page) to the newest message.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [turns, isSending]);

  const submit = () => {
    const text = draft.trim();
    if (!text || isSending || done) return;
    onSend(text);
    setDraft('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto flex-1 min-h-0 flex flex-col bg-slate-900/60 backdrop-blur-md rounded-2xl border border-slate-700/50 overflow-hidden">
      {/* Messages */}
      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto p-4 md:p-6 space-y-4">
        {turns.map((turn, i) => (
          <div key={i} className="flex flex-col gap-1">
            <span
              className={`text-[11px] font-semibold uppercase tracking-wider ${
                turn.role === 'interviewer' ? 'text-emerald-400' : 'text-sky-400'
              }`}
            >
              {turn.role === 'interviewer' ? 'Noor' : 'You'}
            </span>
            <div
              className={`rounded-2xl px-4 py-2.5 max-w-[85%] text-sm leading-relaxed whitespace-pre-wrap ${
                turn.role === 'interviewer'
                  ? 'bg-slate-800/70 text-slate-200 self-start rounded-tl-sm'
                  : 'bg-sky-500/15 border border-sky-500/20 text-slate-100 self-end rounded-tr-sm'
              }`}
            >
              {turn.text}
            </div>
          </div>
        ))}
        {isSending && (
          <div className="flex items-center gap-2 text-slate-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            Noor is typing…
          </div>
        )}
        {done && (
          <p className="text-center text-xs text-slate-500 italic pt-2">
            The interview has ended. Click “End interview” to see your summary.
          </p>
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-slate-700/50 p-3 md:p-4 shrink-0 space-y-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isSending || done}
            rows={1}
            placeholder={done ? 'Interview ended' : 'Type your reply… (Enter to send, Shift+Enter for a new line)'}
            className="flex-1 resize-none bg-slate-800/60 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all max-h-40 disabled:opacity-50"
          />
          <button
            onClick={submit}
            disabled={isSending || done || draft.trim() === ''}
            className="flex items-center justify-center w-12 h-12 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            title="Send"
          >
            {isSending ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
          </button>
        </div>

        <button
          onClick={onEnd}
          className={`w-full flex items-center justify-center gap-2 rounded-xl font-semibold transition-all ${
            done
              ? 'bg-rose-500 hover:bg-rose-600 text-white py-3.5 text-base shadow-lg shadow-rose-500/20'
              : 'bg-slate-800/60 hover:bg-slate-800 text-slate-300 border border-slate-700/60 py-2.5 text-sm'
          }`}
        >
          <Square className="w-4 h-4" fill="currentColor" />
          End interview
        </button>
      </div>
    </div>
  );
}
