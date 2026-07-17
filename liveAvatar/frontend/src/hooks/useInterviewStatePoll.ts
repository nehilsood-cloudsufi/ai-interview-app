import { useEffect, useState } from 'react';
import { API_URL } from '../config';
import type { InterviewStateResponse } from '../types';

const POLL_INTERVAL_MS = 4000;

/**
 * Polls GET /api/interview/{interviewId}/state every 4s while both an
 * interviewId exists and the session is active (plus one immediate fetch on
 * activation). Errors are non-fatal: a 404 (state not yet known) or a network
 * blip simply skips the tick and keeps the last good data.
 */
export function useInterviewStatePoll(
  interviewId: string | null,
  active: boolean,
): { interviewState: InterviewStateResponse | null; error: string | null } {
  const [interviewState, setInterviewState] = useState<InterviewStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!interviewId || !active) return;

    let cancelled = false;
    const fetchState = async () => {
      try {
        const res = await fetch(`${API_URL}/api/interview/${interviewId}/state`);
        if (!res.ok) return; // e.g. 404 while unknown — keep last good data
        const data: InterviewStateResponse = await res.json();
        if (!cancelled) {
          setInterviewState(data);
          setError(null);
        }
      } catch {
        if (!cancelled) setError('Failed to fetch interview state');
      }
    };

    fetchState();
    const interval = setInterval(fetchState, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [interviewId, active]);

  return { interviewState, error };
}
