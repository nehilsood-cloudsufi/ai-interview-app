import { useState, type FormEvent } from 'react';
import { FileText, Loader2, Paperclip, Search, X } from 'lucide-react';

export interface ScoutFormFields {
  companyName: string;
  representativeName: string;
  representativeRole: string;
  extraContext: string;
  documents: File[];
  transcript: string;
}

interface ScoutFormProps {
  onSubmit: (fields: ScoutFormFields) => void;
  isSubmitting: boolean;
}

const MAX_DOCUMENTS = 3;

const inputClass =
  'w-full bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-2.5 text-sm text-slate-200 ' +
  'placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50';

// Mirrors the pre-interview intake form's fields (full name/company
// required, role/"anything else"/documents optional) plus a transcript
// field for pasting one in manually - normally the transcript only exists
// once the interview completes, ahead of the interview module piping it in
// automatically.
export function ScoutForm({ onSubmit, isSubmitting }: ScoutFormProps) {
  const [companyName, setCompanyName] = useState('');
  const [representativeName, setRepresentativeName] = useState('');
  const [representativeRole, setRepresentativeRole] = useState('');
  const [extraContext, setExtraContext] = useState('');
  const [documents, setDocuments] = useState<File[]>([]);
  const [transcript, setTranscript] = useState('');

  const busy = isSubmitting;
  const incomplete = !companyName.trim() || !representativeName.trim();

  const addDocuments = (picked: FileList | null) => {
    if (!picked) return;
    const additions = Array.from(picked);
    setDocuments((prev) => [...prev, ...additions].slice(0, MAX_DOCUMENTS));
  };

  const removeDocument = (index: number) => {
    setDocuments((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (incomplete) return;
    onSubmit({
      companyName: companyName.trim(),
      representativeName: representativeName.trim(),
      representativeRole: representativeRole.trim(),
      extraContext: extraContext.trim(),
      documents,
      transcript: transcript.trim(),
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-company-name" className="text-sm font-semibold text-slate-300">
          Company <span className="text-rose-400">*</span>
        </label>
        <input
          id="scout-company-name"
          required
          disabled={busy}
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="Acme Corp"
          className={inputClass}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="scout-rep-name" className="text-sm font-semibold text-slate-300">
            Full Name <span className="text-rose-400">*</span>
          </label>
          <input
            id="scout-rep-name"
            required
            disabled={busy}
            value={representativeName}
            onChange={(e) => setRepresentativeName(e.target.value)}
            placeholder="Jane Doe"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="scout-rep-role" className="text-sm font-semibold text-slate-300">
            Role (optional)
          </label>
          <input
            id="scout-rep-role"
            disabled={busy}
            value={representativeRole}
            onChange={(e) => setRepresentativeRole(e.target.value)}
            placeholder="VP Sales"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-extra-context" className="text-sm font-semibold text-slate-300">
          Anything else about you or your company? (optional)
        </label>
        <textarea
          id="scout-extra-context"
          rows={3}
          disabled={busy}
          value={extraContext}
          onChange={(e) => setExtraContext(e.target.value)}
          placeholder="e.g. what you build, your company website, anything worth checking"
          className={`${inputClass} resize-none`}
        />
      </div>

      <div className="flex flex-col gap-2">
        {documents.length < MAX_DOCUMENTS && (
          <label
            className={`flex items-center gap-2 text-sm font-medium text-slate-400 hover:text-slate-200 transition-colors ${busy ? 'opacity-50' : 'cursor-pointer'}`}
          >
            <Paperclip className="w-4 h-4" />
            Attach documents for context (optional, up to {MAX_DOCUMENTS})
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md"
              multiple
              disabled={busy}
              onChange={(e) => {
                addDocuments(e.target.files);
                e.target.value = '';
              }}
              className="hidden"
            />
          </label>
        )}
        {documents.map((file, index) => (
          <div
            key={`${file.name}-${index}`}
            className="flex items-center gap-2 bg-slate-800/40 border border-slate-700/40 rounded-lg px-3 py-2 text-sm text-slate-300"
          >
            <FileText className="w-4 h-4 shrink-0 text-slate-500" />
            <span className="truncate flex-1">{file.name}</span>
            {!busy && (
              <button
                type="button"
                onClick={() => removeDocument(index)}
                aria-label={`Remove ${file.name}`}
                className="text-slate-500 hover:text-slate-200 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="scout-transcript" className="text-sm font-semibold text-slate-300">
          Interview Transcript (optional)
        </label>
        <textarea
          id="scout-transcript"
          rows={8}
          disabled={busy}
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          placeholder="Paste the interview transcript here, once the interview has completed."
          className={`${inputClass} resize-y font-mono text-xs leading-relaxed`}
        />
        <p className="text-xs text-slate-500 leading-relaxed">
          If provided (along with the context/documents above), Scout also extracts factual claims and runs a
          second, targeted research pass on them.
        </p>
      </div>

      <button
        type="submit"
        disabled={busy || incomplete}
        className="w-full flex items-center justify-center gap-2.5 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-6 py-3 rounded-xl font-bold transition-all duration-500 disabled:opacity-50 disabled:hover:bg-[position:left_center]"
      >
        {isSubmitting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Search className="w-5 h-5" />}
        Run Data Scout
      </button>
    </form>
  );
}
