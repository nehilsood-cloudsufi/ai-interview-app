import { Loader2, Mic, MicOff, Play, Square, Video, VideoOff } from 'lucide-react';
import type { SessionStatus } from '../types';

interface SessionControlsProps {
  status: SessionStatus;
  isUploading: boolean;
  micEnabled: boolean;
  cameraEnabled: boolean;
  onStart: () => void;
  onStop: () => void;
  onToggleMic: () => void;
  onToggleCamera: () => void;
}

export function SessionControls({ status, isUploading, micEnabled, cameraEnabled, onStart, onStop, onToggleMic, onToggleCamera }: SessionControlsProps) {
  if (status === 'disconnected') {
    return (
        <div className="flex flex-col items-center w-full max-w-sm">
            <button onClick={onStart} disabled={isUploading} className="w-full flex items-center justify-center gap-3 bg-gradient-to-r from-indigo-500 via-sky-500 to-indigo-500 bg-[length:200%_auto] hover:bg-[position:right_center] text-white px-8 py-4 rounded-2xl font-bold transition-all duration-500 shadow-[0_0_40px_-10px_rgba(99,102,241,0.5)] hover:shadow-[0_0_60px_-15px_rgba(99,102,241,0.7)] hover:-translate-y-1 disabled:opacity-50 disabled:hover:translate-y-0 text-lg">
                {isUploading ? <Loader2 className="w-6 h-6 animate-spin" /> : <Play className="w-6 h-6 ml-1" fill="currentColor" />}
                {isUploading ? 'Uploading Context...' : 'Start Interview'}
            </button>
        </div>
    );
  }

  return (
      <div className="flex items-center justify-center gap-4">
          {/* Audio/Video Controls */}
          <div className="flex items-center gap-2 bg-slate-900/80 backdrop-blur-xl p-2 rounded-3xl border border-slate-700/50 shadow-2xl">
              <button onClick={onToggleMic} className={`flex items-center justify-center w-14 h-14 rounded-2xl transition-all ${micEnabled ? 'bg-slate-800 hover:bg-slate-700 text-slate-200' : 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'}`} title={micEnabled ? 'Mute Microphone' : 'Unmute Microphone'}>
                  {micEnabled ? <Mic className="w-6 h-6" /> : <MicOff className="w-6 h-6" />}
              </button>
              <button onClick={onToggleCamera} className={`flex items-center justify-center w-14 h-14 rounded-2xl transition-all ${cameraEnabled ? 'bg-slate-800 hover:bg-slate-700 text-slate-200' : 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'}`} title={cameraEnabled ? 'Turn off Camera' : 'Turn on Camera'}>
                  {cameraEnabled ? <Video className="w-6 h-6" /> : <VideoOff className="w-6 h-6" />}
              </button>
          </div>

          {/* End Control */}
          <button onClick={onStop} className="flex items-center justify-center w-14 h-14 bg-rose-500 hover:bg-rose-600 text-white rounded-3xl transition-all shadow-lg shadow-rose-500/20 hover:shadow-rose-500/40 hover:-translate-y-0.5" title="End Interview">
              <Square className="w-5 h-5" fill="currentColor" />
          </button>
      </div>
  );
}
