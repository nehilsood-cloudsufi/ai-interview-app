import { SignalHigh, SignalLow, SignalMedium, SignalZero } from 'lucide-react';
import type { NetworkQuality } from '../types';

export function NetworkIndicator({ networkQuality }: { networkQuality: NetworkQuality }) {
  const icon =
    networkQuality === 'excellent' ? <SignalHigh className="w-4 h-4 text-emerald-500" /> :
    networkQuality === 'good' ? <SignalMedium className="w-4 h-4 text-amber-500" /> :
    networkQuality === 'poor' ? <SignalLow className="w-4 h-4 text-rose-500" /> :
    <SignalZero className="w-4 h-4 text-slate-500" />;

  return (
    <div title={`Network Quality: ${networkQuality}`} className="cursor-help flex items-center bg-slate-800/50 backdrop-blur-md px-2.5 py-1.5 rounded-lg border border-slate-700/50 shadow-sm">
        {icon}
    </div>
  );
}
