import { useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { API_URL } from '../config';
import { useScoutReport } from '../hooks/useScoutReport';
import { ScoutForm, type ScoutFormFields } from './ScoutForm';
import { ScoutPanel } from './ScoutPanel';
import type { ScoutDocumentResponse } from '../types';

interface ScoutModalProps {
  onClose: () => void;
}

// Extracts one attached document's plain text via the standalone
// /api/scout/document endpoint (independent of the intake form's own
// per-interview document upload). Returns null on failure - a failed
// document is skipped rather than blocking the whole scout run.
async function extractDocumentText(file: File): Promise<string | null> {
  try {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${API_URL}/api/scout/document`, { method: 'POST', body: form });
    if (!res.ok) throw new Error(`extraction failed (${res.status})`);
    const data: ScoutDocumentResponse = await res.json();
    return data.text;
  } catch (err) {
    console.error(`Failed to extract text from ${file.name}:`, err);
    return null;
  }
}

// On-demand Data Scout Agent entry point: the form (company/rep/context/
// documents/transcript) hands off to the results panel once submitted. Kept
// entirely separate from the interview flow - this is a standalone research
// tool.
export function ScoutModal({ onClose }: ScoutModalProps) {
  const scout = useScoutReport();
  const [submitted, setSubmitted] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const [documentError, setDocumentError] = useState<string | null>(null);

  const handleSubmit = async (fields: ScoutFormFields) => {
    setIsPreparing(true);
    setDocumentError(null);

    const extracted = await Promise.all(fields.documents.map(extractDocumentText));
    const documents = extracted.filter((text): text is string => text !== null);
    if (documents.length < fields.documents.length) {
      // Give the vendor a beat to read the notice before the view swaps to
      // results, same pattern as StartScreen's own document-upload notices.
      setDocumentError('One or more documents could not be read and were skipped.');
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }

    setIsPreparing(false);
    setSubmitted(true);
    scout.run({
      company_name: fields.companyName,
      representative_name: fields.representativeName,
      representative_role: fields.representativeRole || null,
      extra_context: fields.extraContext || null,
      documents: documents.length > 0 ? documents : null,
      transcript: fields.transcript || null,
    });
  };

  const handleDismiss = () => {
    scout.dismiss();
    onClose();
  };

  if (!submitted) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
        <div className="w-full max-w-lg bg-slate-900 rounded-2xl border border-slate-700/60 shadow-2xl p-6">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-lg font-bold text-white">Data Scout Agent</h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <ScoutForm onSubmit={handleSubmit} isSubmitting={isPreparing} />
          {documentError && (
            <div className="mt-3 flex items-start gap-2 text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span className="text-sm">{documentError}</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <ScoutPanel
      visible={scout.visible}
      isGenerating={scout.isGenerating}
      internetFindings={scout.internetFindings}
      interviewClaims={scout.interviewClaims}
      sources={scout.sources}
      companyName={scout.companyName}
      error={scout.error}
      onDismiss={handleDismiss}
    />
  );
}
