import { useState } from 'react';
import { Check, Pencil, UserRound, X } from 'lucide-react';
import type { VendorProfile } from '../types';

interface ProfileCardProps {
  profile: VendorProfile;
  isOnboarding: boolean;
  onSave: (changes: Partial<VendorProfile>) => Promise<boolean>;
}

const EM_DASH = '—';

function display(value: string | null): string {
  return value && value.trim() !== '' ? value : EM_DASH;
}

interface FieldConfig {
  key: keyof VendorProfile;
  label: string;
}

const FIELDS: FieldConfig[] = [
  { key: 'contact_name', label: 'Name' },
  { key: 'contact_role', label: 'Role' },
  { key: 'company_name', label: 'Company' },
];

/**
 * Shows the profile Noor has captured, for the whole interview (both avatar
 * and chat modes) - "here's what I captured" while onboarding is still in
 * progress (`isOnboarding`), "Your details" afterward. The vendor can correct
 * any field at any time via Edit; `onSave` PATCHes only the changed fields,
 * which the backend then locks against the Host's own profile_updates going
 * forward. App mounts it above the transcript (avatar) or atop the chat column
 * (chat) for as long as useVendorProfile has a profile to show.
 */
export function ProfileCard({ profile, isOnboarding, onSave }: ProfileCardProps) {
  const [editing, setEditing] = useState(false);
  // Seeded from `profile` the moment Edit is clicked, then fully local - a
  // poll tick landing mid-edit must not clobber what the vendor is typing.
  const [seed, setSeed] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startEdit = () => {
    const values: Record<string, string> = {};
    for (const { key } of FIELDS) values[key] = profile[key] ?? '';
    setSeed(values);
    setDraft(values);
    setError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setError(null);
  };

  const save = async () => {
    const changes: Partial<VendorProfile> = {};
    for (const { key } of FIELDS) {
      if (draft[key] !== seed[key]) {
        (changes as Record<string, string>)[key] = draft[key];
      }
    }

    if (Object.keys(changes).length === 0) {
      setEditing(false);
      return;
    }

    setSaving(true);
    setError(null);
    const ok = await onSave(changes);
    setSaving(false);

    if (ok) {
      setEditing(false);
    } else {
      setError('Could not save — please try again.');
    }
  };

  return (
    <div className="w-full shrink-0 bg-slate-900/60 backdrop-blur-md rounded-xl border border-slate-800 overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-slate-800">
        <div className="flex items-center gap-2 min-w-0">
          <UserRound className="w-4 h-4 text-slate-400 shrink-0" />
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 truncate">
            {isOnboarding ? "Here's what I captured" : 'Your details'}
          </span>
        </div>
        {!editing && (
          <button
            onClick={startEdit}
            className="flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-slate-200 transition-colors shrink-0"
          >
            <Pencil className="w-3.5 h-3.5" />
            Edit
          </button>
        )}
      </div>

      {editing ? (
        <div className="p-4 flex flex-col gap-2.5">
          {FIELDS.map(({ key, label }) => (
            <div key={key} className="flex flex-col gap-1">
              <label htmlFor={`profile-${key}`} className="text-xs text-slate-500">
                {label}
              </label>
              <input
                id={`profile-${key}`}
                type="text"
                value={draft[key] ?? ''}
                onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                disabled={saving}
                className="w-full bg-slate-800/60 border border-slate-700/60 rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/70 focus:border-transparent transition-all disabled:opacity-50"
              />
            </div>
          ))}

          {error && <p className="text-xs text-rose-400">{error}</p>}

          <div className="flex items-center gap-2 mt-1">
            <button
              onClick={save}
              disabled={saving}
              className="flex-1 flex items-center justify-center gap-1.5 bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-200 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              <Check className="w-3.5 h-3.5" />
              Save
            </button>
            <button
              onClick={cancelEdit}
              disabled={saving}
              className="flex-1 flex items-center justify-center gap-1.5 bg-slate-800/60 hover:bg-slate-800 text-slate-300 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
            >
              <X className="w-3.5 h-3.5" />
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <dl className="p-4 grid grid-cols-2 gap-x-4 gap-y-2">
          {FIELDS.map(({ key, label }) => (
            <div key={key} className="contents">
              <dt className="text-xs text-slate-500">{label}</dt>
              <dd className="text-sm text-slate-200 text-right truncate">{display(profile[key])}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
