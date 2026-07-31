import { useEffect, useState } from 'react';
import type { SessionStatus } from '../types';

/**
 * Tracks how long the avatar session has been live, in whole seconds.
 *
 * Returns the elapsed second count. While `status` is 'connected' it ticks
 * up once per second (via a setInterval owned here); any other status resets
 * the count to 0. The interval is cleared on unmount and whenever `status`
 * changes, so switching away from 'connected' both stops the clock and zeroes
 * it. The caller decides how to display this (elapsed vs. a computed
 * countdown) — the hook only counts up.
 */
export function useSessionTimer(status: SessionStatus): number {
  const [sessionDuration, setSessionDuration] = useState(0);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (status === 'connected') {
      interval = setInterval(() => setSessionDuration(prev => prev + 1), 1000);
    } else {
      setSessionDuration(0);
    }
    return () => clearInterval(interval);
  }, [status]);

  return sessionDuration;
}
