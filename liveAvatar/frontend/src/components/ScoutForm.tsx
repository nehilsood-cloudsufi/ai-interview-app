import { useState, type FormEvent } from 'react';
import { Loader2, Search } from 'lucide-react';

export interface ScoutFormFields {
  companyName: string;
  companyWebsite: string;
  representativeName: string;
  representativeRole: string;
  transcript: string;
}

interface ScoutFormProps {
  onSubmit: (fields: ScoutFormFields) => void;
  isSubmitting: boolean;
}

const inputClass =
  'w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-2.5 text-sm text-slate-200 ' +
  'placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all';

export function ScoutForm({ onSubmit, isSubmitting }: ScoutFormProps) {
  const [companyName, setCompanyName] = useState('');
  const [companyWebsite, setCompanyWebsite] = useState('');
  const [representativeName, setRepresentativeName] = useState('');
  const [representativeRole, setRepresentativeRole] = useState('');
  const [transcript, setTranscript] = useState('');

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!companyName.trim()) return;
    onSubmit({
      companyName: companyName.trim(),
      companyWebsite: companyWebsite.trim(),
      representativeName: representativeName.trim(),
      representativeRole: representativeRole.trim(),
      transcript: transcript.trim(),
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-company-name" className="text-sm font-semibold text-slate-300">
          Company Name <span className="text-rose-400">*</span>
        </label>
        <input
          id="scout-company-name"
          required
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="Acme Corp"
          className={inputClass}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-company-website" className="text-sm font-semibold text-slate-300">
          Company Website
        </label>
        <input
          id="scout-company-website"
          type="url"
          value={companyWebsite}
          onChange={(e) => setCompanyWebsite(e.target.value)}
          placeholder="https://acme.example.com"
          className={inputClass}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="scout-rep-name" className="text-sm font-semibold text-slate-300">
            Representative Name
          </label>
          <input
            id="scout-rep-name"
            value={representativeName}
            onChange={(e) => setRepresentativeName(e.target.value)}
            placeholder="Jane Doe"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="scout-rep-role" className="text-sm font-semibold text-slate-300">
            Representative Role
          </label>
          <input
            id="scout-rep-role"
            value={representativeRole}
            onChange={(e) => setRepresentativeRole(e.target.value)}
            placeholder="VP Sales"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-transcript" className="text-sm font-semibold text-slate-300">
          Interview Transcript
        </label>
        <textarea
          id="scout-transcript"
          rows={8}
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          placeholder="Paste the interview transcript here. Manual entry for now, until the interview module pipes it in automatically."
          className={`${inputClass} resize-y font-mono text-xs leading-relaxed`}
        />
        <p className="text-xs text-slate-500 leading-relaxed">
          Optional. If provided, Scout also extracts factual claims and runs a second, targeted research pass on them.
        </p>
      </div>

      <button
        type="submit"
        disabled={isSubmitting || !companyName.trim()}
        className="w-full flex items-center justify-center gap-2.5 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-6 py-3 rounded-xl font-bold transition-all duration-500 disabled:opacity-50 disabled:hover:bg-[position:left_center]"
      >
        {isSubmitting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Search className="w-5 h-5" />}
        Run Data Scout
      </button>
    </form>
  );
}
