import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL } from '../config';
import type { ChatResponse, TranscriptTurn } from '../types';

// Local, frontend-only opening bubble mirroring the avatar's spoken opener
// (see backend _gateway_opening_text). The backend's authoritative transcript
// is unaffected — this is purely so the chat column isn't empty on entry.
const GREETING =
  "Hello, and welcome! I'm Noor, and I'll be running today's vendor " +
  'evaluation. To get us started, could you introduce yourself — your ' +
  "name, your role, the company you represent, and that company's website?";

interface UseChatInterviewOptions {
  interviewId: string | null;
  onError?: (message: string | null) => void;
}

export function useChatInterview({ interviewId, onError }: UseChatInterviewOptions) {
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [done, setDone] = useState(false);

  const interviewIdRef = useRef(interviewId);
  useEffect(() => { interviewIdRef.current = interviewId; }, [interviewId]);

  // Mirror so send() always appends against the freshest turns even while a
  // request is in flight.
  const turnsRef = useRef<TranscriptTurn[]>([]);
  const setTurnsSynced = (next: TranscriptTurn[]) => {
    turnsRef.current = next;
    setTurns(next);
  };

  // Enter chat mode. With no carried turns (chat-from-start) we seed the local
  // greeting; on a mid-session switch we keep the transcript already captured.
  const start = useCallback((initialTurns: TranscriptTurn[]) => {
    const seed: TranscriptTurn[] = initialTurns.length > 0
      ? initialTurns
      : [{ role: 'interviewer', text: GREETING, timestamp: Date.now() }];
    setTurnsSynced(seed);
    setDone(false);
    setIsSending(false);
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    const id = interviewIdRef.current;
    if (!trimmed || !id || isSending || done) return;

    onError?.(null);
    const candidateTurn: TranscriptTurn = { role: 'candidate', text: trimmed, timestamp: Date.now() };
    setTurnsSynced([...turnsRef.current, candidateTurn]);
    setIsSending(true);

    try {
      const res = await fetch(`${API_URL}/api/interview/${id}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: trimmed }),
      });
      if (!res.ok) throw new Error('Failed to send message');

      const data: ChatResponse = await res.json();
      setTurnsSynced([
        ...turnsRef.current,
        { role: 'interviewer', text: data.reply, timestamp: Date.now() },
      ]);
      setDone(data.done);
    } catch (err) {
      console.error('Chat send failed:', err);
      onError?.('Could not reach the interviewer. Please try sending that again.');
    } finally {
      setIsSending(false);
    }
  }, [isSending, done, onError]);

  return { turns, isSending, done, start, send };
}
