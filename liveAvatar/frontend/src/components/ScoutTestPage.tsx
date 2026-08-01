import { useState } from 'react';
import { AlertTriangle, ArrowLeft, FlaskConical, Loader2, Play } from 'lucide-react';
import { API_URL } from '../config';
import type { ScoutTestResponse } from '../types';
import { ScoutMarkdown } from './ScoutMarkdown';

// Shared styling for this page's text inputs/textareas - matches
// StartScreen's FIELD_CLASSES so the dev harness still looks like the app.
const FIELD_CLASSES =
  'w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50';

/**
 * DEV-ONLY: a manual testing harness for the Data Scout Agent
 * (app.services.data_scout_agent.run_scout via POST /api/scout-test) - lets
 * a developer enter every input Scout accepts (including the representative's
 * email, which exercises Representative Identity Verification) and see the
 * full rendered report without running a full interview. Runs ONLY Scout -
 * no Evaluator, no Coordinator, nothing persisted.
 *
 * Mounted only at /scout-test, and only in a dev build (see main.tsx) -
 * never linked to from the real interview flow. Deliberately small and
 * self-contained: this is NOT a reintroduction of the removed
 * ScoutForm/ScoutModal/ScoutPanel feature, just a thin form + result viewer.
 */
export function ScoutTestPage() {
  const [companyName, setCompanyName] = useState('');
  const [representativeName, setRepresentativeName] = useState('');
  const [email, setEmail] = useState('');
  const [extraContext, setExtraContext] = useState('');
  const [documentsText, setDocumentsText] = useState('');
  const [transcript, setTranscript] = useState('');

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScoutTestResponse | null>(null);

  const run = async () => {
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${API_URL}/api/scout-test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: companyName.trim(),
          representative_name: representativeName.trim(),
          email: email.trim(),
          extra_context: extraContext,
          documents_text: documentsText,
          transcript,
        }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail;
        throw new Error(detail || `Scout run failed (${res.status})`);
      }
      setResult(await res.json());
    } catch (err) {
      console.error('Scout test run failed:', err);
      setError(err instanceof Error ? err.message : 'Scout run failed');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-slate-950 text-white px-4 py-10">
      <div className="max-w-3xl mx-auto flex flex-col gap-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center shrink-0">
              <FlaskConical className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-white leading-tight">Data Scout Agent — Test Harness</h1>
              <p className="text-xs font-semibold uppercase tracking-wider text-amber-400">Dev only — not a production feature</p>
            </div>
          </div>
          <a href="/" className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm font-medium transition-colors">
            <ArrowLeft className="w-4 h-4" />
            Back
          </a>
        </div>

        <p className="text-sm text-slate-400 leading-relaxed -mt-2">
          Runs only <code className="text-xs bg-slate-800/60 rounded px-1">run_scout</code> directly with the inputs
          below — no Evaluator, no Coordinator, nothing persisted. Scout is slow (web search + Gemini calls); expect
          this to take up to a minute.
        </p>

        {/* Form */}
        <div className="flex flex-col gap-3 bg-slate-900/60 border border-slate-700/50 rounded-2xl p-5">
          <input
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            disabled={pending}
            placeholder="Company Name *"
            className={FIELD_CLASSES}
          />
          <input
            value={representativeName}
            onChange={(e) => setRepresentativeName(e.target.value)}
            disabled={pending}
            placeholder="Representative Name"
            className={FIELD_CLASSES}
          />
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={pending}
            placeholder="Email (exercises Representative Identity Verification)"
            className={FIELD_CLASSES}
          />
          <textarea
            value={extraContext}
            onChange={(e) => setExtraContext(e.target.value)}
            disabled={pending}
            rows={3}
            placeholder="About / Extra Context (the intake form's 'anything else about you or your company' text)"
            className={`${FIELD_CLASSES} resize-none`}
          />
          <textarea
            value={documentsText}
            onChange={(e) => setDocumentsText(e.target.value)}
            disabled={pending}
            rows={4}
            placeholder="Document Text (a single pasted document body, optional)"
            className={`${FIELD_CLASSES} resize-none`}
          />
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            disabled={pending}
            rows={6}
            placeholder="Transcript (a pasted interview transcript, optional)"
            className={`${FIELD_CLASSES} resize-none`}
          />

          <button
            onClick={run}
            disabled={pending || !companyName.trim()}
            className="w-full flex items-center justify-center gap-2.5 bg-gradient-to-r from-amber-500 to-orange-500 text-white px-6 py-3 rounded-xl font-bold transition-all disabled:opacity-50 mt-1"
          >
            {pending ? <Loader2 className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5" />}
            {pending ? 'Running Scout…' : 'Run Scout'}
          </button>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2.5">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span className="text-sm">{error}</span>
          </div>
        )}

        {result && (
          <div className="flex flex-col gap-4 bg-slate-900/60 border border-slate-700/50 rounded-2xl p-5">
            <ScoutMarkdown markdown={result.internet_findings} />

            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer font-semibold text-slate-300">
                Raw representative_verification (structured)
              </summary>
              <pre className="mt-2 whitespace-pre-wrap break-all bg-slate-950/60 rounded-lg p-3 text-[11px] leading-relaxed">
                {JSON.stringify(result.representative_verification, null, 2)}
              </pre>
            </details>

            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer font-semibold text-slate-300">Raw sources (structured, large)</summary>
              <pre className="mt-2 whitespace-pre-wrap break-all bg-slate-950/60 rounded-lg p-3 text-[11px] leading-relaxed max-h-96 overflow-y-auto">
                {JSON.stringify(result.sources, null, 2)}
              </pre>
            </details>

            <p className="text-xs text-slate-500">
              interview_claims: {result.interview_claims.length} · findings_ok: {String(result.findings_ok)} ·
              company_website: {result.company_website ?? 'none'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
