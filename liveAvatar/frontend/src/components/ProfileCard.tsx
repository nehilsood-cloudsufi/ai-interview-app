import { UserRound } from 'lucide-react';
import type { VendorProfile } from '../types';

interface ProfileCardProps {
  profile: VendorProfile;
}

const EM_DASH = '—';

function display(value: string | null): string {
  return value && value.trim() !== '' ? value : EM_DASH;
}

// Onboarding-phase aid: "here's what I captured" about the vendor's company,
// mirroring what Noor confirms conversationally (no accept/confirm button -
// confirmation stays spoken, this card is read-only).
export function ProfileCard({ profile }: ProfileCardProps) {
  const rows: { label: string; value: string | null }[] = [
    { label: 'Name', value: profile.contact_name },
    { label: 'Role', value: profile.contact_role },
    { label: 'Company', value: profile.company_name },
    { label: 'Website', value: profile.website },
  ];

  return (
    <div className="w-full shrink-0 bg-slate-900/60 backdrop-blur-md rounded-xl border border-slate-800 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800">
        <UserRound className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Here's what I captured</span>
      </div>

      <dl className="p-4 grid grid-cols-2 gap-x-4 gap-y-2">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-xs text-slate-500">{row.label}</dt>
            <dd className="text-sm text-slate-200 text-right truncate">{display(row.value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
