import { useState } from 'react';
import { MessageSquareText, Sparkles, X } from 'lucide-react';
import { useLiveAvatarSession } from './hooks/useLiveAvatarSession';
import { useNetworkQuality } from './hooks/useNetworkQuality';
import { useConcurrencyPoll } from './hooks/useConcurrencyPoll';
import { useSessionTimer } from './hooks/useSessionTimer';
import { useInterviewSummary } from './hooks/useInterviewSummary';
import { useChatInterview } from './hooks/useChatInterview';
import { useVendorProfile } from './hooks/useVendorProfile';
import { StartScreen } from './components/StartScreen';
import { ChatPanel } from './components/ChatPanel';
import { NetworkIndicator } from './components/NetworkIndicator';
import { ConcurrencyBadge } from './components/ConcurrencyBadge';
import { SpeakingIndicator } from './components/SpeakingIndicator';
import { AvatarVideoPanel } from './components/AvatarVideoPanel';
import { LocalVideoPanel } from './components/LocalVideoPanel';
import { TranscriptPanel } from './components/TranscriptPanel';
import { ProfileCard } from './components/ProfileCard';
import { SummaryPanel } from './components/SummaryPanel';
import { SessionControls } from './components/SessionControls';
import { ErrorToast } from './components/ErrorToast';
import { formatTime } from './utils/formatTime';
import { SHOW_SELF_VIEW } from './config';
import type { InterviewMode } from './types';

function App() {
  const [error, setError] = useState<string | null>(null);
  // Two views: the start screen (pick a mode) and the interview room.
  const [view, setView] = useState<'start' | 'interview'>('start');
  // Interview mode. One-way: an avatar session can switch to chat, never back.
  const [mode, setMode] = useState<InterviewMode>('avatar');
  const [interviewId, setInterviewId] = useState<string | null>(null);
  const [networkBannerDismissed, setNetworkBannerDismissed] = useState(false);

  const networkQuality = useNetworkQuality();
  const concurrencyCount = useConcurrencyPoll();
  const summary = useInterviewSummary(interviewId);
  const chat = useChatInterview({ interviewId, onError: setError });
  const vendorProfile = useVendorProfile(interviewId, view === 'interview');

  const {
    status,
    speakingState,
    micEnabled,
    cameraEnabled,
    transcript,
    videoRef,
    localVideoRef,
    startSession,
    stopSession,
    toggleMic,
    toggleCamera,
  } = useLiveAvatarSession({ interviewId, onError: setError, onSessionEnd: summary.finalize });

  const sessionDuration = useSessionTimer(status);

  const enterInterview = (id: string, chosenMode: InterviewMode) => {
    setInterviewId(id);
    setMode(chosenMode);
    if (chosenMode === 'chat') chat.start([]);
    setView('interview');
  };

  // One-way switch from a live avatar session to text chat. The captured
  // transcript is carried into the chat; the normal end-of-session finalize is
  // suppressed so the same interview continues rather than being scored now.
  const switchToChat = () => {
    chat.start(transcript);
    setMode('chat');
    if (status !== 'disconnected') stopSession({ suppressSessionEnd: true });
  };

  const showNetworkBanner =
    mode === 'avatar' && status === 'connected' && networkQuality === 'poor' && !networkBannerDismissed;

  // Card is visible for the whole interview (session/chat active) once the
  // first poll response has arrived - gated by view/mode via each render
  // branch below (avatar: only once connected; chat: only in chat mode).
  // In chat mode it also hides once finalize has begun (summary.visible) -
  // by then state.pipeline_status is set and a PATCH would just 409, so the
  // card should disappear rather than invite an edit that can't land. Avatar
  // mode is already safely gated on the connected session (the session ends
  // before finalize starts), so it doesn't need this extra check.
  const showProfileCard = Boolean(vendorProfile.profile) && !(mode === 'chat' && summary.visible);

  if (view === 'start') {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <StartScreen onStart={enterInterview} />
        <ErrorToast error={error} onDismiss={() => setError(null)} />
      </div>
    );
  }

  return (
    // h-screen (definite), NOT min-h-screen: a column flex container with auto
    // height sizes itself from its children's CONTENT, so a growing transcript
    // stretched every shell past the viewport ("UI keeps growing", seen live in
    // chat mode 2026-07-20) instead of letting the min-h-0 chain scroll it.
    <div className="h-screen bg-slate-950 text-white flex flex-col overflow-hidden">
      <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-slate-900 via-slate-950 to-black">
        {/* Header */}
        <div className="p-4 md:px-8 md:py-5 flex justify-between items-center shrink-0 border-b border-slate-800/60">
          <div className="flex items-center gap-4 min-w-0">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 to-sky-500 flex items-center justify-center shrink-0">
                <Sparkles className="w-4.5 h-4.5 text-white" />
              </div>
              <div className="min-w-0">
                <h1 className="text-base md:text-lg font-bold leading-tight truncate">Vendor Interview</h1>
                <p className="text-xs text-slate-500 leading-tight truncate">
                  {mode === 'chat' ? 'Text chat · ' : ''}Hosted by Noor
                </p>
              </div>
            </div>
            {mode === 'avatar' && <NetworkIndicator networkQuality={networkQuality} />}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <ConcurrencyBadge count={concurrencyCount} />
            {mode === 'avatar' && status === 'connected' && (
              <span className="font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg text-sm font-semibold tracking-wider shadow-inner">
                {formatTime(sessionDuration)}
              </span>
            )}
            {mode === 'avatar' && (
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
            )}
          </div>
        </div>

        {mode === 'chat' ? (
          /* Text-chat column */
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-4 md:px-8 md:py-6 gap-3">
            {showProfileCard && (
              <div className="w-full max-w-2xl mx-auto shrink-0">
                <ProfileCard
                  profile={vendorProfile.profile!}
                  isOnboarding={vendorProfile.isOnboarding}
                  onSave={vendorProfile.saveProfile}
                />
              </div>
            )}
            <ChatPanel
              turns={chat.turns}
              isSending={chat.isSending}
              done={chat.done}
              onSend={chat.send}
              onEnd={() => summary.finalize(chat.turns, interviewId)}
            />
          </div>
        ) : (
          <>
            {/* Video Area */}
            <div className="flex-1 relative flex overflow-hidden p-4 md:px-8 pb-4">
              {/* min-h-0: keeps children (esp. the transcript) bounded so their
                  content scrolls internally instead of stretching the row. */}
              <div className="flex-1 self-stretch min-h-0 flex flex-col md:flex-row items-stretch justify-center gap-4 transition-all duration-500">
                <AvatarVideoPanel status={status} speakingState={speakingState} videoRef={videoRef} />
                {SHOW_SELF_VIEW && (
                  <LocalVideoPanel status={status} speakingState={speakingState} cameraEnabled={cameraEnabled} micEnabled={micEnabled} localVideoRef={localVideoRef} />
                )}

                {status === 'connected' && (
                  // grid-rows-[auto_1fr]: the ProfileCard (row 1, natural
                  // height) sits above the transcript (row 2, fills whatever
                  // is left) without the transcript losing its own min-h-0
                  // bound when the card isn't rendered - see comment above on
                  // why nothing here may grow with content.
                  <div className="w-full md:w-80 lg:w-96 shrink-0 grid grid-rows-[auto_1fr] gap-3 min-h-0">
                    {showProfileCard && (
                      <div className="row-start-1">
                        <ProfileCard
                          profile={vendorProfile.profile!}
                          isOnboarding={vendorProfile.isOnboarding}
                          onSave={vendorProfile.saveProfile}
                        />
                      </div>
                    )}
                    <div className="row-start-2 min-h-0 flex">
                      <TranscriptPanel turns={transcript} />
                    </div>
                  </div>
                )}

                <SpeakingIndicator visible={status === 'connected'} speakingState={speakingState} />
              </div>
            </div>

            {/* Poor-network auto-suggest */}
            {showNetworkBanner && (
              <div className="px-4 md:px-8 shrink-0">
                <div className="mx-auto max-w-2xl flex items-center justify-between gap-3 bg-amber-500/10 border border-amber-500/25 rounded-xl px-4 py-2.5">
                  <span className="text-sm text-amber-200">Network looks weak — switch to text chat?</span>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={switchToChat}
                      className="flex items-center gap-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-100 text-sm font-semibold px-3 py-1.5 rounded-lg transition-colors"
                    >
                      <MessageSquareText className="w-4 h-4" />
                      Switch to chat
                    </button>
                    <button
                      onClick={() => setNetworkBannerDismissed(true)}
                      className="p-1.5 rounded-lg text-amber-300/80 hover:text-amber-100 hover:bg-amber-500/20 transition-colors"
                      aria-label="Dismiss"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Controls */}
            <div className="p-6 md:px-8 md:pb-8 flex flex-col justify-center items-center gap-4 shrink-0">
              <SessionControls
                status={status}
                micEnabled={micEnabled}
                cameraEnabled={cameraEnabled}
                onStart={startSession}
                onStop={() => stopSession()}
                onToggleMic={toggleMic}
                onToggleCamera={toggleCamera}
              />
              {status === 'connected' && (
                <button
                  onClick={switchToChat}
                  className="flex items-center gap-1.5 text-slate-400 hover:text-slate-200 text-sm font-medium transition-colors"
                >
                  <MessageSquareText className="w-4 h-4" />
                  Switch to text chat
                </button>
              )}
            </div>
          </>
        )}

        <ErrorToast error={error} onDismiss={() => setError(null)} />
      </div>

      <SummaryPanel
        visible={summary.visible}
        isGenerating={summary.isGenerating}
        summary={summary.summary}
        turns={summary.turns}
        sessionId={summary.sessionId}
        error={summary.error}
        pipelineStatus={summary.pipelineStatus}
        scorecard={summary.scorecard}
        insights={summary.insights}
        recommendation={summary.recommendation}
        onDismiss={summary.dismiss}
      />
    </div>
  );
}

export default App;
