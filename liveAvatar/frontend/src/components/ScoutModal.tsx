import { useState } from 'react';
import { X } from 'lucide-react';
import { useScoutReport } from '../hooks/useScoutReport';
import { ScoutForm, type ScoutFormFields } from './ScoutForm';
import { ScoutPanel } from './ScoutPanel';

interface ScoutModalProps {
  onClose: () => void;
}

// On-demand Data Scout Agent entry point: the form (company/website/rep/
// transcript) hands off to the results panel once submitted. Kept entirely
// separate from the interview flow - this is a standalone research tool.
export function ScoutModal({ onClose }: ScoutModalProps) {
  const scout = useScoutReport();
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (fields: ScoutFormFields) => {
    setSubmitted(true);
    scout.run({
      company_name: fields.companyName,
      company_website: fields.companyWebsite || null,
      representative_name: fields.representativeName || null,
      representative_role: fields.representativeRole || null,
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
          <ScoutForm onSubmit={handleSubmit} isSubmitting={false} />
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
