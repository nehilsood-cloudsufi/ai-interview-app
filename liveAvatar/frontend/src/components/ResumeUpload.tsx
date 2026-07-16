import { Upload, X } from 'lucide-react';
import type { ChangeEvent } from 'react';
import type { SessionStatus } from '../types';

interface ResumeUploadProps {
  files: File[];
  onFileChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (index: number) => void;
  status: SessionStatus;
  apiKey: string;
  onApiKeyChange: (value: string) => void;
}

export function ResumeUpload({ files, onFileChange, onRemoveFile, status, apiKey, onApiKeyChange }: ResumeUploadProps) {
  return (
    <div className="w-full md:w-80 flex flex-col border-b md:border-b-0 md:border-r border-slate-800 bg-slate-900/40 shrink-0 z-10 shadow-2xl">
        <div className="p-6 border-b border-slate-800 flex justify-between items-center bg-slate-900/80 backdrop-blur-md shrink-0">
            <h2 className="text-base font-semibold text-slate-200 tracking-wide">Context Documents</h2>
            <span className="text-xs font-medium px-2 py-1 bg-slate-800 rounded-full text-slate-400">{files.length}/5</span>
        </div>

        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-4">
            {files.length === 0 ? (
                <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-center text-sm p-8 border-2 border-dashed border-slate-800 rounded-2xl bg-slate-900/20">
                    <Upload className="w-10 h-10 mb-4 opacity-20" />
                    <p className="font-medium text-slate-400">No documents yet</p>
                    <p className="mt-2 text-xs opacity-70 leading-relaxed max-w-[200px]">Drop resumes or portfolios here for the AI to reference during the interview.</p>
                </div>
            ) : (
                files.map((file, idx) => (
                    <div key={idx} className="flex justify-between items-center bg-slate-800/50 p-4 rounded-xl border border-slate-700/50 group transition-all hover:border-slate-600 hover:bg-slate-800 shadow-sm">
                        <div className="flex flex-col overflow-hidden mr-3">
                            <span className="text-sm text-slate-200 truncate font-medium">{file.name}</span>
                            <span className="text-xs text-slate-500 mt-0.5">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
                        </div>
                        <button onClick={() => onRemoveFile(idx)} disabled={status !== 'disconnected'} className="p-2 text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-slate-500 shrink-0">
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                ))
            )}
        </div>

        {status === 'disconnected' && (
            <div className="p-6 border-t border-slate-800 bg-slate-900/60 backdrop-blur-md shrink-0 flex flex-col gap-4">
                {files.length < 5 && (
                    <label className="flex items-center justify-center gap-2 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 px-4 py-4 rounded-xl cursor-pointer transition-all border border-indigo-500/30 hover:border-indigo-500/50 hover:shadow-lg hover:shadow-indigo-500/10 group mb-2">
                        <Upload className="w-5 h-5 group-hover:-translate-y-0.5 transition-transform" />
                        <span className="text-sm font-semibold tracking-wide">Upload File</span>
                        <input type="file" multiple accept=".pdf,.docx,.txt" className="hidden" onChange={onFileChange} disabled={status !== 'disconnected'} />
                    </label>
                )}

                <input
                    type="password"
                    placeholder="LiveAvatar API Key (optional)"
                    value={apiKey}
                    onChange={e => onApiKeyChange(e.target.value)}
                    className="w-full bg-slate-800/50 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors"
                />
            </div>
        )}
    </div>
  );
}
