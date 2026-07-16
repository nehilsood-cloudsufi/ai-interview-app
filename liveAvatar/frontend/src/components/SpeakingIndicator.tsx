import type { SpeakingState } from '../types';

interface SpeakingIndicatorProps {
  visible: boolean;
  speakingState: SpeakingState;
}

export function SpeakingIndicator({ visible, speakingState }: SpeakingIndicatorProps) {
  if (!visible) return null;
  return (
      <div className="absolute top-8 left-1/2 -translate-x-1/2 bg-slate-900/90 backdrop-blur-xl px-6 py-3 rounded-2xl flex items-center gap-4 border border-slate-700/50 shadow-2xl z-10">
          <div className="flex items-end gap-1.5 h-4">
              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-1' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-2' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-2' : 'text-slate-600 h-1'}`} />
              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-2' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-3' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-3' : 'text-slate-600 h-1.5'}`} />
              <div className={`w-1.5 bg-current rounded-full ${speakingState === 'user_speaking' ? 'text-sky-400 animate-audio-bar-3' : speakingState === 'avatar_speaking' ? 'text-emerald-400 animate-audio-bar-1' : speakingState === 'processing' ? 'text-amber-400 animate-pulse h-2' : 'text-slate-600 h-1'}`} />
          </div>
          <span className="text-sm font-semibold tracking-wide text-slate-200">
              {speakingState === 'user_speaking' ? 'You are speaking' : speakingState === 'avatar_speaking' ? 'Avatar speaking' : speakingState === 'processing' ? 'Thinking...' : 'Listening...'}
          </span>
      </div>
  );
}
