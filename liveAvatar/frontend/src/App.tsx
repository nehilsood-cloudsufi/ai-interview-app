import { useState } from 'react';
import { ArrowLeft, Sparkles } from 'lucide-react';
import { useLiveAvatarSession } from './hooks/useLiveAvatarSession';
import { useNetworkQuality } from './hooks/useNetworkQuality';
import { useConcurrencyPoll } from './hooks/useConcurrencyPoll';
import { useSessionTimer } from './hooks/useSessionTimer';
import { useResumeFiles } from './hooks/useResumeFiles';
import { useInterviewSummary } from './hooks/useInterviewSummary';
import { useInterviewStatePoll } from './hooks/useInterviewStatePoll';
import { LandingPage } from './components/LandingPage';
import { NetworkIndicator } from './components/NetworkIndicator';
import { ConcurrencyBadge } from './components/ConcurrencyBadge';
import { SpeakingIndicator } from './components/SpeakingIndicator';
import { AvatarVideoPanel } from './components/AvatarVideoPanel';
import { LocalVideoPanel } from './components/LocalVideoPanel';
import { TranscriptPanel } from './components/TranscriptPanel';
import { SummaryPanel } from './components/SummaryPanel';
import { ScorecardPanel } from './components/ScorecardPanel';
import { SessionControls } from './components/SessionControls';
import { ErrorToast } from './components/ErrorToast';
import { formatTime } from './utils/formatTime';
import type { VendorProfile } from './types';

function App() {
  const [error, setError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState('');
  // Two-page flow: landing (vendor intake) -> interview room.
  const [view, setView] = useState<'landing' | 'interview'>('landing');
  // Gateway mode: set once the vendor profile is saved; passed to the session
  // hook so /api/session and /api/session/stop carry the interview_id.
  const [interviewId, setInterviewId] = useState<string | null>(null);
  const [vendorProfile, setVendorProfile] = useState<VendorProfile | null>(null);

  const vendorDocs = useResumeFiles(setError);
  const networkQuality = useNetworkQuality();
  const concurrencyCount = useConcurrencyPoll();
  const summary = useInterviewSummary(interviewId);

  const {
    status,
    speakingState,
    micEnabled,
    cameraEnabled,
    isUploading,
    transcript,
    videoRef,
    localVideoRef,
    startSession,
    stopSession,
    toggleMic,
    toggleCamera,
  } = useLiveAvatarSession({ apiKey, files: [], interviewId, onError: setError, onSessionEnd: summary.finalize });

  const sessionDuration = useSessionTimer(status);

  // Gateway mode only: live scorecard polling while the session is connected.
  const { interviewState } = useInterviewStatePoll(interviewId, status === 'connected');

  if (view === 'landing') {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <LandingPage
          files={vendorDocs.files}
          onFileChange={vendorDocs.handleFileChange}
          onRemoveFile={vendorDocs.removeFile}
          apiKey={apiKey}
          onApiKeyChange={setApiKey}
          onSubmitted={(id, profile) => {
            setInterviewId(id);
            setVendorProfile(profile);
            setView('interview');
          }}
          onError={setError}
        />
        <ErrorToast error={error} onDismiss={() => setError(null)} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-white flex flex-col overflow-hidden">
      <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black">
        {/* Header */}
        <div className="p-4 md:px-8 md:py-5 flex justify-between items-center shrink-0 border-b border-slate-800/60">
          <div className="flex items-center gap-4 min-w-0">
            {status === 'disconnected' && (
              <button
                onClick={() => setView('landing')}
                className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm font-medium transition-colors shrink-0"
              >
                <ArrowLeft className="w-4 h-4" />
                Edit profile
              </button>
            )}
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-sky-500 flex items-center justify-center shrink-0">
                <Sparkles className="w-4.5 h-4.5 text-white" />
              </div>
              <div className="min-w-0">
                <h1 className="text-base md:text-lg font-bold leading-tight truncate">
                  {vendorProfile?.companyName ? `${vendorProfile.companyName} — Vendor Interview` : 'Vendor Interview'}
                </h1>
                <p className="text-xs text-slate-500 leading-tight truncate">
                  {vendorProfile?.contactName ? `${vendorProfile.contactName}${vendorProfile.contactRole ? `, ${vendorProfile.contactRole}` : ''} · ` : ''}
                  Hosted by Noor
                </p>
              </div>
            </div>
            <NetworkIndicator networkQuality={networkQuality} />
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <ConcurrencyBadge count={concurrencyCount} />
            {status === 'connected' && (
              <span className="font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wider shadow-inner">
                {formatTime(sessionDuration)}
              </span>
            )}
            <div className="flex items-center gap-2.5 bg-slate-800/50 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700/50">
              <div
                className={`w-2.5 h-2.5 rounded-full shadow-sm ${
                  status === 'connected'
                    ? 'bg-emerald-500 shadow-emerald-500/50'
                    : status === 'connecting'
                      ? 'bg-amber-500 animate-pulse shadow-amber-500/50'
                      : 'bg-rose-500 shadow-rose-500/50'
                }`}
              />
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">{status}</span>
            </div>
          </div>
        </div>

        {/* Video Area */}
        <div className="flex-1 relative flex overflow-hidden p-4 md:px-8 pb-4">
          <div className="flex-1 self-stretch flex flex-col md:flex-row items-stretch justify-center gap-4 transition-all duration-500">
            <AvatarVideoPanel status={status} speakingState={speakingState} isUploading={isUploading} videoRef={videoRef} />
            <LocalVideoPanel status={status} speakingState={speakingState} cameraEnabled={cameraEnabled} micEnabled={micEnabled} localVideoRef={localVideoRef} />

            {status === 'connected' && <TranscriptPanel turns={transcript} />}
            {status === 'connected' && interviewId && <ScorecardPanel state={interviewState} />}

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

      <SummaryPanel
        visible={summary.visible}
        isGenerating={summary.isGenerating}
        summary={summary.summary}
        turns={summary.turns}
        sessionId={summary.sessionId}
        error={summary.error}
        scorecard={summary.scorecard}
        insights={summary.insights}
        followup={summary.followup}
        onDismiss={summary.dismiss}
      />
    </div>
  );
}

export default App;
