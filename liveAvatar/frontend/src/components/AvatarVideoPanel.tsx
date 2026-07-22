import { Loader2, Video } from 'lucide-react';
import type { RefObject } from 'react';
import type { SessionStatus, SpeakingState } from '../types';

interface AvatarVideoPanelProps {
  status: SessionStatus;
  speakingState: SpeakingState;
  videoRef: RefObject<HTMLVideoElement | null>;
}

/**
 * The AI interviewer's video tile in avatar mode. Shows a "Ready to start"
 * placeholder while disconnected, a spinner while connecting, and the live
 * <video> (via `videoRef`, fed by the LiveAvatar SDK) once connected - with a
 * glow ring keyed to `speakingState` (emerald when the avatar speaks, amber
 * while it thinks). App mounts it in the avatar-mode video row; purely
 * presentational, all state lives in useLiveAvatarSession.
 */
export function AvatarVideoPanel({ status, speakingState, videoRef }: AvatarVideoPanelProps) {
  return (
      <div className={`flex-1 relative flex items-center justify-center rounded-3xl overflow-hidden shadow-2xl transition-all duration-300 ${
          status === 'disconnected' ? 'bg-slate-900/50 border border-slate-800' : 'bg-black'
      } ${
          status === 'connected' && speakingState === 'avatar_speaking' ? 'ring-2 ring-emerald-500/50 ring-offset-4 ring-offset-slate-950' :
          status === 'connected' && speakingState === 'processing' ? 'ring-2 ring-amber-500/50 ring-offset-4 ring-offset-slate-950' :
          status === 'connected' ? 'border border-slate-800' : ''
      }`}>
          {status === 'disconnected' && (
             <div className="flex flex-col items-center text-slate-500">
                 <div className="w-24 h-24 rounded-full bg-slate-800/50 flex items-center justify-center mb-6 shadow-inner">
                     <Video className="w-10 h-10 opacity-20" />
                 </div>
                 <p className="text-lg font-medium text-slate-400">Ready to start</p>
                 <p className="text-sm mt-2 opacity-60">Waiting for you to begin the interview</p>
             </div>
          )}
          {status === 'connecting' && (
             <div className="flex flex-col items-center text-slate-400 bg-slate-900/50 w-full h-full justify-center backdrop-blur-sm">
                 <Loader2 className="w-12 h-12 mb-6 animate-spin text-indigo-500" />
                 <p className="text-lg font-medium">Connecting to avatar...</p>
             </div>
          )}
          <video ref={videoRef} autoPlay playsInline className={`w-full h-full object-cover ${status === 'connected' ? 'opacity-100' : 'opacity-0 absolute'}`} />
          {status === 'connected' && (
              <div className="absolute bottom-6 left-6 bg-slate-950/80 backdrop-blur-md px-4 py-2 rounded-xl text-sm font-semibold text-slate-200 shadow-xl border border-slate-700/50 flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  AI Interviewer
              </div>
          )}
      </div>
  );
}
