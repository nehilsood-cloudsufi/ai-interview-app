import { useEffect, useState } from 'react';
import { MessageSquareText } from 'lucide-react';
import { useLiveAvatarSession } from './hooks/useLiveAvatarSession';
import { useAvatarStall } from './hooks/useAvatarStall';
import { useNetworkQuality } from './hooks/useNetworkQuality';
import { useConcurrencyPoll } from './hooks/useConcurrencyPoll';
import { useSessionTimer } from './hooks/useSessionTimer';
import { useInterviewSummary } from './hooks/useInterviewSummary';
import { useChatInterview } from './hooks/useChatInterview';
import { useVendorProfile } from './hooks/useVendorProfile';
import { StartScreen } from './components/StartScreen';
import { ChatPanel } from './components/ChatPanel';
import { InterviewHeader } from './components/InterviewHeader';
import { NetworkBanner } from './components/NetworkBanner';
import { AvatarStallBanner } from './components/AvatarStallBanner';
import { SpeakingIndicator } from './components/SpeakingIndicator';
import { AvatarVideoPanel } from './components/AvatarVideoPanel';
import { LocalVideoPanel } from './components/LocalVideoPanel';
import { TranscriptPanel } from './components/TranscriptPanel';
import { ProfileCard } from './components/ProfileCard';
import { SummaryPanel } from './components/SummaryPanel';
import { SessionControls } from './components/SessionControls';
import { ErrorToast } from './components/ErrorToast';
import { SHOW_SELF_VIEW } from './config';
import type { InterviewMode } from './types';

/**
 * Root composition for the interview experience. Renders one of two views:
 * the StartScreen (pick avatar vs. text-chat mode and mint an interview_id)
 * and the interview room. The room runs in one of two modes — avatar (live
 * HeyGen session) or text chat — and supports a ONE-WAY switch from avatar to
 * chat (never back), carrying the captured transcript into the chat so the
 * same interview continues rather than being finalized early.
 *
 * This component is composition-only: it wires the hooks (session, chat,
 * profile, summary, network, concurrency, timer) to the presentational
 * components and holds just the small amount of view/mode state that
 * coordinates them.
 */
function App() {
  const [error, setError] = useState<string | null>(null);
  // Two views: the start screen (pick a mode) and the interview room.
  const [view, setView] = useState<'start' | 'interview'>('start');
  // Interview mode. One-way: an avatar session can switch to chat, never back.
  const [mode, setMode] = useState<InterviewMode>('avatar');
  const [interviewId, setInterviewId] = useState<string | null>(null);
  // Prod tier only: the session length picked on the start screen (seconds).
  const [plannedSeconds, setPlannedSeconds] = useState<number | null>(null);
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

  const enterInterview = (id: string, chosenMode: InterviewMode, durationSeconds?: number) => {
    setInterviewId(id);
    setMode(chosenMode);
    setPlannedSeconds(durationSeconds ?? null);
    if (chosenMode === 'chat') chat.start([]);
    setView('interview');
  };

  // Prod tier: the picked session length -> the timer badge counts DOWN so
  // the vendor is conscious of the limit (the Host wraps up in the final
  // minute server-side). Dev tier: null -> elapsed timer as before.
  const remainingSeconds = plannedSeconds !== null ? Math.max(0, plannedSeconds - sessionDuration) : null;

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

  // Interview reached END while the avatar session is still live: give the
  // closing line time to finish speaking, then stop the session ourselves
  // (normal stop path -> finalize -> summary). Without this, every further
  // utterance re-spoke the canned closing in a loop (seen live 2026-07-22)
  // while prod-tier credits kept burning.
  useEffect(() => {
    if (!(mode === 'avatar' && status === 'connected' && vendorProfile.interviewDone)) return;
    const timer = setTimeout(() => stopSession(), 8000);
    return () => clearTimeout(timer);
    // stopSession is recreated per render but stable in behavior; deps kept
    // to the actual trigger conditions so the timer isn't re-armed each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, status, vendorProfile.interviewDone]);

  // Turn-taking stall: HeyGen can drop the avatar's cancelled reply and wait
  // for fresh user speech - both sides then sit silent. After 20s of neither
  // party speaking, nudge the user (speaking again resumes the interview).
  // Dismissal lasts only for the current stall episode; it re-arms once
  // someone speaks.
  const avatarStalled = useAvatarStall(mode === 'avatar' && status === 'connected', speakingState);
  const [stallBannerDismissed, setStallBannerDismissed] = useState(false);
  useEffect(() => {
    if (!avatarStalled) setStallBannerDismissed(false);
  }, [avatarStalled]);
  const showStallBanner = avatarStalled && !stallBannerDismissed && !showNetworkBanner;

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
        <InterviewHeader
          mode={mode}
          status={status}
          networkQuality={networkQuality}
          concurrencyCount={concurrencyCount}
          remainingSeconds={remainingSeconds}
          sessionDuration={sessionDuration}
        />

        {mode === 'chat' ? (
          /* Text-chat column */
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-4 md:px-8 md:py-6 gap-3">
            {showProfileCard && (
              <div className="w-full max-w-2xl mx-auto shrink-0">
                <ProfileCard
                  profile={vendorProfile.profile!}
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
              <NetworkBanner onSwitchToChat={switchToChat} onDismiss={() => setNetworkBannerDismissed(true)} />
            )}

            {/* Stalled-conversation nudge (see useAvatarStall) */}
            {showStallBanner && (
              <AvatarStallBanner onSwitchToChat={switchToChat} onDismiss={() => setStallBannerDismissed(true)} />
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
