import { useEffect, useState } from 'react';
import type { SessionStatus } from '../types';

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
