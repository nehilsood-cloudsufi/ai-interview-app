import { useState, type ChangeEvent } from 'react';
import { Building2, CheckCircle2, Loader2, Upload, X } from 'lucide-react';
import { API_URL } from '../config';
import type { SessionStatus, VendorProfile, VendorProfileResponse } from '../types';

interface VendorIntakeFormProps {
  files: File[];
  onFileChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (index: number) => void;
  status: SessionStatus;
  interviewId: string | null;
  onSubmitted: (interviewId: string) => void;
  onError: (message: string | null) => void;
}

const inputClass = "w-full bg-slate-800/50 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors";

export function VendorIntakeForm({ files, onFileChange, onRemoveFile, status, interviewId, onSubmitted, onError }: VendorIntakeFormProps) {
  const [profile, setProfile] = useState<VendorProfile>({ companyName: '', website: '', contactName: '', contactRole: '' });
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (status !== 'disconnected') return null;

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

      const res = await fetch(`${API_URL}/api/vendor-profile`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || 'Failed to save vendor profile');
      }

      const data: VendorProfileResponse = await res.json();
      onSubmitted(data.interview_id);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to save vendor profile');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="w-full md:w-80 flex flex-col border-b md:border-b-0 md:border-r border-slate-800 bg-slate-900/40 shrink-0 z-10 shadow-2xl">
        <div className="p-6 border-b border-slate-800 flex justify-between items-center bg-slate-900/80 backdrop-blur-md shrink-0">
            <h2 className="text-base font-semibold text-slate-200 tracking-wide flex items-center gap-2">
                <Building2 className="w-4 h-4 text-indigo-400" />
                Vendor Profile
            </h2>
            {interviewId && (
                <span className="flex items-center gap-1.5 text-xs font-medium px-2 py-1 bg-emerald-500/10 border border-emerald-500/20 rounded-full text-emerald-400">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Saved
                </span>
            )}
        </div>

        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-4">
            <input type="text" placeholder="Company name *" value={profile.companyName} onChange={setField('companyName')} className={inputClass} />
            <input type="text" placeholder="Website" value={profile.website} onChange={setField('website')} className={inputClass} />
            <input type="text" placeholder="Contact name *" value={profile.contactName} onChange={setField('contactName')} className={inputClass} />
            <input type="text" placeholder="Role" value={profile.contactRole} onChange={setField('contactRole')} className={inputClass} />

            <div className="flex flex-col gap-3 mt-2">
                <div className="flex justify-between items-center">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Company documents</span>
                    <span className="text-xs font-medium px-2 py-1 bg-slate-800 rounded-full text-slate-400">{files.length}/5</span>
                </div>

                {files.map((file, idx) => (
                    <div key={idx} className="flex justify-between items-center bg-slate-800/50 p-3 rounded-xl border border-slate-700/50 group transition-all hover:border-slate-600 hover:bg-slate-800 shadow-sm">
                        <div className="flex flex-col overflow-hidden mr-3">
                            <span className="text-sm text-slate-200 truncate font-medium">{file.name}</span>
                            <span className="text-xs text-slate-500 mt-0.5">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                        </div>
                        <button onClick={() => onRemoveFile(idx)} className="p-2 text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors shrink-0">
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                ))}

                {files.length < 5 && (
                    <label className="flex items-center justify-center gap-2 bg-slate-800/50 hover:bg-slate-800 text-slate-300 px-4 py-3 rounded-xl cursor-pointer transition-all border border-slate-700 hover:border-slate-600 group">
                        <Upload className="w-4 h-4 group-hover:-translate-y-0.5 transition-transform" />
                        <span className="text-sm font-medium">Add company documents</span>
                        <input type="file" multiple accept=".pdf,.docx,.txt" className="hidden" onChange={onFileChange} />
                    </label>
                )}
            </div>
        </div>

        <div className="p-6 border-t border-slate-800 bg-slate-900/60 backdrop-blur-md shrink-0">
            <button
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="w-full flex items-center justify-center gap-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 px-4 py-4 rounded-xl transition-all border border-indigo-500/30 hover:border-indigo-500/50 hover:shadow-lg hover:shadow-indigo-500/10 text-sm font-semibold tracking-wide disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-indigo-500/10 disabled:hover:border-indigo-500/30 disabled:hover:shadow-none"
            >
                {isSubmitting ? <Loader2 className="w-5 h-5 animate-spin" /> : null}
                {isSubmitting ? 'Saving...' : interviewId ? 'Update vendor profile' : 'Save vendor profile'}
            </button>
        </div>
    </div>
  );
}
