import { useEffect, useRef, useState } from 'react';
import { LiveAvatarSession, SessionEvent, AgentEventsEnum } from '@heygen/liveavatar-web-sdk';
import { API_URL, SHOW_SELF_VIEW } from '../config';
import type { SessionStatus, SpeakingState, TranscriptTurn } from '../types';

interface UseLiveAvatarSessionOptions {
  // Gateway mode: id returned by /api/interview. Sent on /api/session so the
  // backend provisions the per-interview Custom LLM, and on every
  // /api/session/stop so those resources get torn down.
  interviewId?: string | null;
  onError: (message: string | null) => void;
  // Called once when a session ends (stop button or server disconnect), before
  // local state is reset — receives the full transcript and the session id.
  // Skipped when stopSession is called with { suppressSessionEnd: true } (the
  // switch-to-chat path, where the chat hook continues the same interview).
  onSessionEnd?: (turns: TranscriptTurn[], sessionId: string | null) => void;
}

interface StopOptions {
  suppressSessionEnd?: boolean;
}

/**
 * Owns the full LiveAvatar (HeyGen) session lifecycle for one avatar
 * interview: creating the backend session, wiring the SDK's stream/speaking/
 * transcription events, managing mic + local camera, capturing the transcript,
 * and tearing everything down cleanly (including orphaned sessions left by a
 * crash/reload and the beforeunload path).
 *
 * Returns session state and controls:
 * - `status` ('disconnected' | 'connecting' | 'connected'), `speakingState`,
 *   `micEnabled`, `cameraEnabled`, `transcript` (turns captured live from the
 *   SDK's transcription events).
 * - `videoRef` / `localVideoRef` — attach points for the avatar and self-view.
 * - `startSession()` — POSTs /api/session, opens the SDK session, subscribes
 *   its event handlers. `stopSession({ suppressSessionEnd })` — tears down
 *   both the backend (POST /api/session/stop) and the SDK; suppressSessionEnd
 *   skips the onSessionEnd handoff (the avatar→chat switch, where chat owns
 *   the transcript). `toggleMic()` / `toggleCamera()`.
 *
 * Lifecycle: on mount it clears any orphaned session from a previous load;
 * event subscriptions live for the session's duration and are removed on
 * cleanup; a beforeunload listener fires a keepalive stop so a closing tab
 * still releases backend resources. `onSessionEnd` fires once when a session
 * ends (stop button or server-side SESSION_DISCONNECTED), handing the captured
 * transcript + session id to the caller for finalize — unless suppressed.
 */
export function useLiveAvatarSession({ interviewId, onError, onSessionEnd }: UseLiveAvatarSessionOptions) {
  const [session, setSession] = useState<LiveAvatarSession | null>(null);
  const [status, setStatus] = useState<SessionStatus>('disconnected');
  const [speakingState, setSpeakingState] = useState<SpeakingState>('idle');
  const [micEnabled, setMicEnabled] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(SHOW_SELF_VIEW);
  const [transcript, setTranscript] = useState<TranscriptTurn[]>([]);

  const interviewIdRef = useRef(interviewId);
  useEffect(() => { interviewIdRef.current = interviewId; }, [interviewId]);

  // When set, the next cleanupSession skips onSessionEnd (used for the one-way
  // switch to text chat, where finalize must NOT run — the chat continues).
  const suppressSessionEndRef = useRef(false);

  // Guards against double-posting /api/session/stop for the same token: the
  // SDK can emit SESSION_DISCONNECTED during a user-initiated stopSession
  // (which already posts stop itself), and this ref stops the disconnect
  // handler from posting a second time for that same session.
  const stopPostedForTokenRef = useRef<string | null>(null);

  // Shared body for every /api/session/stop call site. The interview id is also
  // persisted to localStorage alongside the session token so orphaned-session
  // cleanup (after a crash/reload) can still tear down gateway resources.
  const buildStopBody = (sessionToken: string) => JSON.stringify({
    session_token: sessionToken,
    interview_id: localStorage.getItem('liveavatar_interview_id') || interviewIdRef.current || undefined,
  });

  // The one POST /api/session/stop. Call sites differ only in which token they
  // send and what happens afterwards (catch/finally chains stay at the call
  // site); the beforeunload path passes keepalive so the request survives the
  // page teardown. Deliberately not awaited anywhere - backend teardown is
  // fire-and-forget from the UI's perspective.
  const postStopSession = (sessionToken: string, init?: { keepalive?: boolean }) =>
    fetch(`${API_URL}/api/session/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: buildStopBody(sessionToken),
      ...(init?.keepalive ? { keepalive: true } : {}),
    });

  // Forget the persisted session (token + interview id) - used after every
  // successful or attempted teardown so orphan cleanup can't fire twice.
  const clearStoredSession = () => {
    localStorage.removeItem('liveavatar_session_token');
    localStorage.removeItem('liveavatar_interview_id');
  };

  // Latest onSessionEnd without re-subscribing the event handlers on every render.
  const onSessionEndRef = useRef(onSessionEnd);
  useEffect(() => { onSessionEndRef.current = onSessionEnd; }, [onSessionEnd]);

  const videoRef = useRef<HTMLVideoElement>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const localStreamRef = useRef<MediaStream | null>(null);

  // Ref mirror of the transcript so it survives state resets during cleanup.
  const turnsRef = useRef<TranscriptTurn[]>([]);
  const sessionIdRef = useRef<string | null>(null);

  const addTurn = (turn: TranscriptTurn) => {
    turnsRef.current = [...turnsRef.current, turn];
    setTranscript(turnsRef.current);
  };

  const cleanupSession = (s: LiveAvatarSession) => {
    s.removeAllListeners();

    // Hand off the captured transcript before we wipe local state. Fires on both
    // the stop-button path and the server-side SESSION_DISCONNECTED path — unless
    // this stop was a switch to text chat, which owns the transcript instead.
    if (!suppressSessionEndRef.current && turnsRef.current.length > 0) {
      onSessionEndRef.current?.(turnsRef.current, sessionIdRef.current);
    }
    suppressSessionEndRef.current = false;
    turnsRef.current = [];
    sessionIdRef.current = null;
    setTranscript([]);

    setSession(null);
    setStatus('disconnected');
    setSpeakingState('idle');
    setMicEnabled(false);
    setCameraEnabled(SHOW_SELF_VIEW);
    clearStoredSession();

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach(track => track.stop());
      localStreamRef.current = null;
    }
  };

  const startSession = async () => {
    try {
      setStatus('connecting');
      onError(null);
      turnsRef.current = [];
      sessionIdRef.current = null;
      setTranscript([]);

      const response = await fetch(`${API_URL}/api/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          interview_id: interviewId || undefined,
        }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || 'Failed to create session on backend');
      }

      const { session_token, session_id } = await response.json();
      sessionIdRef.current = session_id ?? null;
      localStorage.setItem('liveavatar_session_token', session_token);
      if (interviewId) {
        localStorage.setItem('liveavatar_interview_id', interviewId);
      } else {
        localStorage.removeItem('liveavatar_interview_id');
      }

      const newSession = new LiveAvatarSession(session_token);

      newSession.on(SessionEvent.SESSION_STREAM_READY, async () => {
        setStatus('connected');
        if (videoRef.current) newSession.attach(videoRef.current);

        // Self-view disabled: skip camera acquisition entirely so no camera
        // permission prompt fires (mic is still needed for voice chat).
        if (!SHOW_SELF_VIEW) {
          setCameraEnabled(false);
          return;
        }

        try {
          const stream = await navigator.mediaDevices.getUserMedia({ video: true });
          localStreamRef.current = stream;
          setCameraEnabled(true);
          if (localVideoRef.current) localVideoRef.current.srcObject = stream;
        } catch (e) {
          console.error("Failed to access local camera:", e);
          setCameraEnabled(false);
        }
      });

      newSession.on(SessionEvent.SESSION_DISCONNECTED, () => {
        // Server-ended sessions (e.g. the dev tier's ~1-min sandbox cap) never
        // go through stopSession, so post the stop here too - this both drops
        // the concurrency badge immediately (rather than waiting on the
        // backend TTL) and tears down the leaked per-interview HeyGen
        // llm-config/secret/context. Guarded by the ref so a
        // SESSION_DISCONNECTED fired during a user-initiated stopSession
        // (which already posted) doesn't double-post for the same token.
        const activeToken = localStorage.getItem('liveavatar_session_token');
        if (activeToken && stopPostedForTokenRef.current !== activeToken) {
          stopPostedForTokenRef.current = activeToken;
          postStopSession(activeToken).catch(console.error);
        }
        cleanupSession(newSession);
      });

      newSession.on(AgentEventsEnum.AVATAR_SPEAK_STARTED, () => setSpeakingState('avatar_speaking'));
      newSession.on(AgentEventsEnum.AVATAR_SPEAK_ENDED, () => setSpeakingState('idle'));
      newSession.on(AgentEventsEnum.USER_SPEAK_STARTED, () => setSpeakingState('user_speaking'));
      newSession.on(AgentEventsEnum.USER_SPEAK_ENDED, () => setSpeakingState('processing'));

      // Transcript capture: final (non-chunk) transcription events, one per completed turn.
      newSession.on(AgentEventsEnum.AVATAR_TRANSCRIPTION, (e) => {
        if (e.text?.trim()) addTurn({ role: 'interviewer', text: e.text.trim(), timestamp: Date.now() });
      });
      newSession.on(AgentEventsEnum.USER_TRANSCRIPTION, (e) => {
        if (e.text?.trim()) addTurn({ role: 'candidate', text: e.text.trim(), timestamp: Date.now() });
      });

      await newSession.start();
      setSession(newSession);

    } catch (err: any) {
      console.error(err);
      onError(err.message || 'An error occurred connecting to the avatar.');
      setStatus('disconnected');

      const orphanedToken = localStorage.getItem('liveavatar_session_token');
      if (orphanedToken) {
        postStopSession(orphanedToken).catch(console.error).finally(clearStoredSession);
      }
    }
  };

  const stopSession = async (options: StopOptions = {}) => {
    if (session) {
      suppressSessionEndRef.current = options.suppressSessionEnd === true;
      // Tear down the backend side (concurrency counter + per-interview LLM
      // config/secret/context) BEFORE cleanupSession wipes the stored token.
      // Without this, an explicit End click never reached /api/session/stop
      // at all: cleanupSession removes the token synchronously, so the
      // [session] effect teardown below found nothing to stop - gateway
      // resources leaked on every normally-ended session.
      const activeToken = localStorage.getItem('liveavatar_session_token');
      if (activeToken) {
        stopPostedForTokenRef.current = activeToken;
        postStopSession(activeToken).catch(console.error);
      }
      try { await session.stop(); } catch (e) { console.error("Error stopping session:", e); }
      cleanupSession(session);
    }
  };

  const toggleMic = async () => {
    if (!session) return;
    try {
      if (micEnabled) {
        await session.voiceChat.stop();
        setMicEnabled(false);
      } else {
        await session.voiceChat.start();
        setMicEnabled(true);
      }
    } catch (e) { console.error("Failed to toggle mic:", e); }
  };

  const toggleCamera = () => {
    if (localStreamRef.current) {
      const videoTrack = localStreamRef.current.getVideoTracks()[0];
      if (videoTrack) {
        videoTrack.enabled = !videoTrack.enabled;
        setCameraEnabled(videoTrack.enabled);
      }
    }
  };

  // Clean up any orphaned session left over from a previous page load
  // (e.g. the tab crashed before beforeunload could fire).
  useEffect(() => {
    const orphanedToken = localStorage.getItem('liveavatar_session_token');
    if (orphanedToken) {
      postStopSession(orphanedToken).catch(console.error).finally(clearStoredSession);
    }
    // postStopSession/clearStoredSession are stable for the life of the hook;
    // this must run exactly once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleBeforeUnload = () => {
      const activeToken = localStorage.getItem('liveavatar_session_token');
      if (activeToken) {
        // keepalive lets the request outlive the closing page; errors are moot
        // mid-unload, so no catch.
        postStopSession(activeToken, { keepalive: true });
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      if (session) {
        session.stop().catch(console.error);
        const activeToken = localStorage.getItem('liveavatar_session_token');
        if (activeToken) {
          postStopSession(activeToken).catch(console.error);
        }
      }
    };
  }, [session]);

  return {
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
  };
}
