import { useEffect, useRef, useState } from 'react';
import { LiveAvatarSession, SessionEvent, AgentEventsEnum } from '@heygen/liveavatar-web-sdk';
import { API_URL, DEFAULT_CONTEXT_ID, DEFAULT_LLM_CONFIG_ID } from '../config';
import type { SessionStatus, SpeakingState } from '../types';

interface UseLiveAvatarSessionOptions {
  apiKey: string;
  files: File[];
  onError: (message: string | null) => void;
}

export function useLiveAvatarSession({ apiKey, files, onError }: UseLiveAvatarSessionOptions) {
  const [session, setSession] = useState<LiveAvatarSession | null>(null);
  const [status, setStatus] = useState<SessionStatus>('disconnected');
  const [speakingState, setSpeakingState] = useState<SpeakingState>('idle');
  const [micEnabled, setMicEnabled] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(true);
  const [isUploading, setIsUploading] = useState(false);

  const apiKeyRef = useRef(apiKey);
  useEffect(() => { apiKeyRef.current = apiKey; }, [apiKey]);

  const videoRef = useRef<HTMLVideoElement>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const localStreamRef = useRef<MediaStream | null>(null);

  const cleanupSession = (s: LiveAvatarSession) => {
    s.removeAllListeners();
    setSession(null);
    setStatus('disconnected');
    setSpeakingState('idle');
    setMicEnabled(false);
    setCameraEnabled(true);
    localStorage.removeItem('liveavatar_session_token');

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach(track => track.stop());
      localStreamRef.current = null;
    }
  };

  const startSession = async () => {
    try {
      setStatus('connecting');
      onError(null);
      setIsUploading(true);

      let currentContextId = DEFAULT_CONTEXT_ID;

      if (files.length > 0) {
        const formData = new FormData();
        files.forEach(file => formData.append('files', file));
        if (apiKey) formData.append('api_key', apiKey);

        const uploadRes = await fetch(`${API_URL}/api/upload-resume`, {
          method: 'POST',
          body: formData,
        });

        if (!uploadRes.ok) {
          const errData = await uploadRes.json();
          throw new Error(errData.detail || 'Failed to upload documents');
        }

        const uploadData = await uploadRes.json();
        currentContextId = uploadData.context_id;
      }
      setIsUploading(false);

      const response = await fetch(`${API_URL}/api/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          context_id: currentContextId,
          llm_configuration_id: DEFAULT_LLM_CONFIG_ID,
          api_key: apiKey || undefined,
        }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => null);
        throw new Error(errData?.detail || 'Failed to create session on backend');
      }

      const { session_token } = await response.json();
      localStorage.setItem('liveavatar_session_token', session_token);

      const newSession = new LiveAvatarSession(session_token);

      newSession.on(SessionEvent.SESSION_STREAM_READY, async () => {
        setStatus('connected');
        if (videoRef.current) newSession.attach(videoRef.current);

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

      newSession.on(SessionEvent.SESSION_DISCONNECTED, () => cleanupSession(newSession));

      newSession.on(AgentEventsEnum.AVATAR_SPEAK_STARTED, () => setSpeakingState('avatar_speaking'));
      newSession.on(AgentEventsEnum.AVATAR_SPEAK_ENDED, () => setSpeakingState('idle'));
      newSession.on(AgentEventsEnum.USER_SPEAK_STARTED, () => setSpeakingState('user_speaking'));
      newSession.on(AgentEventsEnum.USER_SPEAK_ENDED, () => setSpeakingState('processing'));

      await newSession.start();
      setSession(newSession);

    } catch (err: any) {
      console.error(err);
      onError(err.message || 'An error occurred connecting to the avatar.');
      setStatus('disconnected');

      const orphanedToken = localStorage.getItem('liveavatar_session_token');
      if (orphanedToken) {
        fetch(`${API_URL}/api/session/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_token: orphanedToken, api_key: apiKeyRef.current || undefined }),
        }).catch(console.error).finally(() => localStorage.removeItem('liveavatar_session_token'));
      }
    }
  };

  const stopSession = async () => {
    if (session) {
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
      fetch(`${API_URL}/api/session/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_token: orphanedToken, api_key: apiKeyRef.current || undefined }),
      }).catch(console.error).finally(() => localStorage.removeItem('liveavatar_session_token'));
    }
  }, []);

  useEffect(() => {
    const handleBeforeUnload = () => {
      const activeToken = localStorage.getItem('liveavatar_session_token');
      if (activeToken) {
        fetch(`${API_URL}/api/session/stop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_token: activeToken, api_key: apiKeyRef.current || undefined }),
          keepalive: true,
        });
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      if (session) {
        session.stop().catch(console.error);
        const activeToken = localStorage.getItem('liveavatar_session_token');
        if (activeToken) {
          fetch(`${API_URL}/api/session/stop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_token: activeToken, api_key: apiKeyRef.current || undefined }),
          }).catch(console.error);
        }
      }
    };
  }, [session]);

  return {
    session,
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
  };
}
