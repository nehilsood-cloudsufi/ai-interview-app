import { useState } from 'react';
import { useLiveAvatarSession } from './hooks/useLiveAvatarSession';
import { useNetworkQuality } from './hooks/useNetworkQuality';
import { useConcurrencyPoll } from './hooks/useConcurrencyPoll';
import { useSessionTimer } from './hooks/useSessionTimer';
import { useResumeFiles } from './hooks/useResumeFiles';
import { ResumeUpload } from './components/ResumeUpload';
import { NetworkIndicator } from './components/NetworkIndicator';
import { ConcurrencyBadge } from './components/ConcurrencyBadge';
import { SpeakingIndicator } from './components/SpeakingIndicator';
import { AvatarVideoPanel } from './components/AvatarVideoPanel';
import { LocalVideoPanel } from './components/LocalVideoPanel';
import { SessionControls } from './components/SessionControls';
import { ErrorToast } from './components/ErrorToast';
import { formatTime } from './utils/formatTime';

function App() {
  const [error, setError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState('');

  const { files, handleFileChange, removeFile } = useResumeFiles(setError);
  const networkQuality = useNetworkQuality();
  const concurrencyCount = useConcurrencyPoll();

  const {
    status,
    speakingState,
    micEnabled,
    cameraEnabled,
    isUploading,
    videoRef,
    localVideoRef,
    startSession,
    stopSession,
    toggleMic,
    toggleCamera,
  } = useLiveAvatarSession({ apiKey, files, onError: setError });

  const sessionDuration = useSessionTimer(status);

  return (
    <div className="min-h-screen bg-slate-950 text-white flex flex-col md:flex-row overflow-hidden">

        <ResumeUpload
            files={files}
            onFileChange={handleFileChange}
            onRemoveFile={removeFile}
            status={status}
            apiKey={apiKey}
            onApiKeyChange={setApiKey}
        />

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black">
            {/* Header */}
            <div className="p-4 md:px-8 md:py-6 flex justify-between items-center shrink-0">
                <div className="flex items-center gap-4">
                    <h1 className="text-xl md:text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-white to-slate-400">Technical Interview</h1>
                    <NetworkIndicator networkQuality={networkQuality} />
                </div>
                <div className="flex items-center gap-4">
                    <ConcurrencyBadge count={concurrencyCount} />
                    {status === 'connected' && (
                        <span className="font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wider shadow-inner">{formatTime(sessionDuration)}</span>
                    )}
                    <div className="flex items-center gap-2.5 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50">
                        <div className={`w-2.5 h-2.5 rounded-full shadow-sm ${status === 'connected' ? 'bg-emerald-500 shadow-emerald-500/50' : status === 'connecting' ? 'bg-amber-500 animate-pulse shadow-amber-500/50' : 'bg-rose-500 shadow-rose-500/50'}`} />
                        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">{status}</span>
                    </div>
                </div>
            </div>

            {/* Video Area */}
            <div className="flex-1 relative flex overflow-hidden p-4 md:px-8 pb-4">
                <div className={`w-full h-full flex flex-col md:flex-row items-stretch justify-center gap-4 transition-all duration-500`}>

                      <AvatarVideoPanel status={status} speakingState={speakingState} isUploading={isUploading} videoRef={videoRef} />
                      <LocalVideoPanel status={status} speakingState={speakingState} cameraEnabled={cameraEnabled} micEnabled={micEnabled} localVideoRef={localVideoRef} />

                  <SpeakingIndicator visible={status === 'connected'} speakingState={speakingState} />
                </div>
            </div>

            {/* Controls */}
            <div className="p-6 md:px-8 md:pb-8 flex flex-col justify-center items-center shrink-0">
                <SessionControls
                    status={status}
                    isUploading={isUploading}
                    micEnabled={micEnabled}
                    cameraEnabled={cameraEnabled}
                    onStart={startSession}
                    onStop={stopSession}
                    onToggleMic={toggleMic}
                    onToggleCamera={toggleCamera}
                />
            </div>

            <ErrorToast error={error} onDismiss={() => setError(null)} />
        </div>
    </div>
  );
}

export default App;
