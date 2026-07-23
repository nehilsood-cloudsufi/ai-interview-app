import { MessageSquareText, X } from 'lucide-react';

interface AvatarStallBannerProps {
  onSwitchToChat: () => void;
  onDismiss: () => void;
}

/**
 * Shown (in place of silence) when useAvatarStall reports the conversation
 * has gone quiet for a while during a connected avatar session — usually
 * HeyGen dropping the avatar's cancelled reply and waiting for fresh user
 * speech. Tells the user the one-step fix (just speak), and offers the
 * text-chat switch as a fallback. Visibility is decided by App; purely
 * presentational, styled like NetworkBanner.
 */
export function AvatarStallBanner({ onSwitchToChat, onDismiss }: AvatarStallBannerProps) {
  return (
    <div className="px-4 md:px-8 shrink-0">
      <div className="mx-auto max-w-2xl flex items-center justify-between gap-3 bg-sky-500/10 border border-sky-500/25 rounded-xl px-4 py-2.5">
        <span className="text-sm text-sky-200">
          Noor's been quiet for a while — just say something to continue the interview.
        </span>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={onSwitchToChat}
            className="flex items-center gap-1.5 bg-sky-500/20 hover:bg-sky-500/30 text-sky-100 text-sm font-semibold px-3 py-1.5 rounded-lg transition-colors"
          >
            <MessageSquareText className="w-4 h-4" />
            Switch to chat
          </button>
          <button
            onClick={onDismiss}
            className="p-1.5 rounded-lg text-sky-300/80 hover:text-sky-100 hover:bg-sky-500/20 transition-colors"
            aria-label="Dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
