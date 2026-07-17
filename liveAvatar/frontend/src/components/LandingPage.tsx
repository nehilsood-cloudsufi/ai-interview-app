import { useState, type ChangeEvent } from 'react';
import { ArrowRight, FileText, Loader2, Radar, ScanSearch, Sparkles, TrendingUp, Upload, X } from 'lucide-react';
import { API_URL } from '../config';
import type { VendorProfile, VendorProfileResponse } from '../types';

interface LandingPageProps {
  files: File[];
  onFileChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (index: number) => void;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  onSubmitted: (interviewId: string, profile: VendorProfile) => void;
  onError: (message: string | null) => void;
}

const inputClass =
  'w-full bg-slate-800/60 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all';

const labelClass = 'text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block';

export function LandingPage({ files, onFileChange, onRemoveFile, apiKey, onApiKeyChange, onSubmitted, onError }: LandingPageProps) {
  const [profile, setProfile] = useState<VendorProfile>({ companyName: '', website: '', contactName: '', contactRole: '' });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const setField = (field: keyof VendorProfile) => (e: ChangeEvent<HTMLInputElement>) =>
    setProfile(prev => ({ ...prev, [field]: e.target.value }));

  const canSubmit = profile.companyName.trim() !== '' && profile.contactName.trim() !== '' && !isSubmitting;

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);
      onError(null);

      const formData = new FormData();
      formData.append('company_name', profile.companyName.trim());
      formData.append('contact_name', profile.contactName.trim());
      if (profile.website.trim()) formData.append('website', profile.website.trim());
      if (profile.contactRole.trim()) formData.append('contact_role', profile.contactRole.trim());
      files.forEach(file => formData.append('files', file));

      const res = await fetch(`${API_URL}/api/vendor-profile`, { method: 'POST', body: formData });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || 'Failed to save vendor profile');
      }

      const data: VendorProfileResponse = await res.json();
      onSubmitted(data.interview_id, profile);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to save vendor profile');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-950/40 via-slate-950 to-black px-4 py-10 overflow-y-auto">
      {/* Brand */}
      <div className="flex flex-col items-center text-center mb-8">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-500 flex items-center justify-center shadow-[0_0_40px_-10px_rgba(99,102,241,0.8)]">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <span className="text-3xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-200 to-slate-400">
            Resonance
          </span>
        </div>
        <p className="text-slate-400 text-sm md:text-base max-w-md leading-relaxed">
          AI-powered vendor evaluation. Tell us about your company, then meet <span className="text-slate-200 font-medium">Noor</span> — your interview host.
        </p>
      </div>

      {/* Intake card */}
      <div className="w-full max-w-2xl bg-slate-900/60 backdrop-blur-xl border border-slate-800 rounded-3xl shadow-2xl overflow-hidden">
        <div className="p-6 md:p-8 flex flex-col gap-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={labelClass}>Company name *</label>
              <input type="text" placeholder="Acme Corp" value={profile.companyName} onChange={setField('companyName')} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Website</label>
              <input type="text" placeholder="acme.com" value={profile.website} onChange={setField('website')} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Contact name *</label>
              <input type="text" placeholder="Jane Doe" value={profile.contactName} onChange={setField('contactName')} className={inputClass} />
            </div>
            <div>
              <label className={labelClass}>Role</label>
              <input type="text" placeholder="Head of Engineering" value={profile.contactRole} onChange={setField('contactRole')} className={inputClass} />
            </div>
          </div>

          {/* Documents */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <label className={labelClass + ' mb-0'}>Company documents</label>
              <span className="text-xs font-medium px-2 py-0.5 bg-slate-800 rounded-full text-slate-400">{files.length}/5</span>
            </div>

            <div className="flex flex-col gap-2">
              {files.map((file, idx) => (
                <div key={idx} className="flex justify-between items-center bg-slate-800/50 px-4 py-2.5 rounded-xl border border-slate-700/50 group transition-all hover:border-slate-600">
                  <div className="flex items-center gap-3 overflow-hidden mr-3">
                    <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                    <span className="text-sm text-slate-200 truncate font-medium">{file.name}</span>
                    <span className="text-xs text-slate-500 shrink-0">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                  </div>
                  <button onClick={() => onRemoveFile(idx)} className="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors shrink-0">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ))}

              {files.length < 5 && (
                <label className="flex flex-col items-center justify-center gap-1.5 border-2 border-dashed border-slate-700/80 hover:border-indigo-500/50 rounded-xl px-4 py-6 cursor-pointer transition-all text-slate-500 hover:text-slate-300 bg-slate-900/30 hover:bg-slate-800/30 group">
                  <Upload className="w-6 h-6 opacity-50 group-hover:opacity-100 group-hover:-translate-y-0.5 transition-all" />
                  <span className="text-sm font-medium">Add capability decks, case studies, certifications…</span>
                  <span className="text-xs opacity-60">PDF, DOCX or TXT — used by our research agent</span>
                  <input type="file" multiple accept=".pdf,.docx,.txt" className="hidden" onChange={onFileChange} />
                </label>
              )}
            </div>
          </div>

          {/* Advanced */}
          <details className="group">
            <summary className="text-xs text-slate-500 hover:text-slate-400 cursor-pointer select-none font-medium">Advanced settings</summary>
            <input
              type="password"
              placeholder="LiveAvatar API Key (optional)"
              value={apiKey}
              onChange={e => onApiKeyChange(e.target.value)}
              className={inputClass + ' mt-3'}
            />
          </details>
        </div>

        <div className="px-6 md:px-8 py-5 border-t border-slate-800 bg-slate-900/80 flex flex-col sm:flex-row items-center justify-between gap-3">
          <span className="text-xs text-slate-500">Takes about 10 minutes. Your scorecard builds live as you talk.</span>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="w-full sm:w-auto flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-7 py-3.5 rounded-xl font-semibold transition-all duration-500 shadow-[0_0_40px_-10px_rgba(99,102,241,0.5)] hover:shadow-[0_0_50px_-12px_rgba(99,102,241,0.7)] hover:-translate-y-0.5 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:translate-y-0 text-sm"
          >
            {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            {isSubmitting ? 'Saving…' : 'Continue to interview'}
            {!isSubmitting && <ArrowRight className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Feature strip */}
      <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 mt-8 text-slate-500">
        <span className="flex items-center gap-2 text-xs font-medium"><TrendingUp className="w-4 h-4 text-indigo-400/70" /> Live scorecard</span>
        <span className="flex items-center gap-2 text-xs font-medium"><Radar className="w-4 h-4 text-sky-400/70" /> Adaptive questions</span>
        <span className="flex items-center gap-2 text-xs font-medium"><ScanSearch className="w-4 h-4 text-emerald-400/70" /> Background research</span>
      </div>
    </div>
  );
}
