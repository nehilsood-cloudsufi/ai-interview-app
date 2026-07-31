import { Mic, MicOff, VideoOff } from 'lucide-react';
import type { RefObject } from 'react';
import type { SessionStatus, SpeakingState } from '../types';

interface LocalVideoPanelProps {
  status: SessionStatus;
  speakingState: SpeakingState;
  cameraEnabled: boolean;
  micEnabled: boolean;
  localVideoRef: RefObject<HTMLVideoElement | null>;
}

/**
 * The vendor's self-view camera tile, shown beside AvatarVideoPanel in avatar
 * mode. Mirrors the local <video> (via `localVideoRef`) once connected, with a
 * "Camera is off" fallback when `cameraEnabled` is false and a sky glow ring
 * while the user speaks; the corner label carries a mic on/off icon. App mounts
 * it only when the SHOW_SELF_VIEW build flag is on; purely presentational.
 */
export function LocalVideoPanel({ status, speakingState, cameraEnabled, micEnabled, localVideoRef }: LocalVideoPanelProps) {
  return (
      <div className={`flex-1 relative flex items-center justify-center rounded-3xl overflow-hidden shadow-2xl transition-all duration-300 ${
          status === 'disconnected' ? 'bg-slate-900/50 border border-slate-800' : 'bg-slate-900/20'
      } ${
          status === 'connected' && speakingState === 'user_speaking' ? 'ring-2 ring-sky-500/50 ring-offset-4 ring-offset-slate-950' :
          status === 'connected' ? 'border border-slate-800' : ''
      }`}>
          {status === 'disconnected' ? (
              <div className="flex flex-col items-center text-slate-500">
                  <VideoOff className="w-12 h-12 mb-4 opacity-20" />
                  <p className="font-medium text-slate-400">Camera Preview</p>
              </div>
          ) : !cameraEnabled ? (
              <div className="flex flex-col items-center text-slate-500 bg-slate-900/80 w-full h-full justify-center backdrop-blur-md">
                  <div className="w-20 h-20 rounded-full bg-slate-800 flex items-center justify-center mb-4">
                      <VideoOff className="w-8 h-8 opacity-50" />
                  </div>
                  <p className="font-medium">Camera is off</p>
              </div>
          ) : (
              <video ref={localVideoRef} autoPlay playsInline muted className={`w-full h-full object-cover scale-x-[-1] ${status === 'connected' ? 'opacity-100' : 'opacity-0 absolute'}`} />
          )}
          {status === 'connected' && (
              <div className="absolute bottom-6 right-6 bg-slate-950/80 backdrop-blur-md px-4 py-2 rounded-xl text-sm font-semibold text-slate-200 shadow-xl border border-slate-700/50 flex items-center gap-2">
                  You
                  {micEnabled ? <Mic className="w-3.5 h-3.5 text-emerald-400" /> : <MicOff className="w-3.5 h-3.5 text-rose-400" />}
              </div>
          )}
      </div>
  );
}
