import { MessageSquareText, X } from 'lucide-react';

interface NetworkBannerProps {
  onSwitchToChat: () => void;
  onDismiss: () => void;
}

/**
 * The poor-network suggestion banner shown between the video area and the
 * session controls when useNetworkQuality reports 'poor' during a connected
 * avatar session. Offers the one-way switch to text chat, or dismissal for
 * the rest of the session. Visibility is decided by App (showNetworkBanner);
 * this component is purely presentational.
 */
export function NetworkBanner({ onSwitchToChat, onDismiss }: NetworkBannerProps) {
  return (
    <div className="px-4 md:px-8 shrink-0">
      <div className="mx-auto max-w-2xl flex items-center justify-between gap-3 bg-amber-500/10 border border-amber-500/25 rounded-xl px-4 py-2.5">
        <span className="text-sm text-amber-200">Network looks weak — switch to text chat?</span>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={onSwitchToChat}
            className="flex items-center gap-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-100 text-sm font-semibold px-3 py-1.5 rounded-lg transition-colors"
          >
            <MessageSquareText className="w-4 h-4" />
            Switch to chat
          </button>
          <button
            onClick={onDismiss}
            className="p-1.5 rounded-lg text-amber-300/80 hover:text-amber-100 hover:bg-amber-500/20 transition-colors"
            aria-label="Dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
