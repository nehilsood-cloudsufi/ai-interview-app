import { useState } from 'react';
import { API_URL } from '../config';
import { useIntervalPoll } from './useIntervalPoll';

/**
 * Polls GET /api/concurrency every 5s for the whole life of the app and
 * returns the backend's active-session count (null until the first response).
 * Feeds the ConcurrencyBadge so demo drivers can spot session leaks / drift.
 * Never stops on failure - a missed poll just keeps the last known value.
 */
export function useConcurrencyPoll(): number | null {
  const [concurrencyCount, setConcurrencyCount] = useState<number | null>(null);

  useIntervalPoll(async () => {
    try {
      const res = await fetch(`${API_URL}/api/concurrency`);
      if (res.ok) {
        const data = await res.json();
        setConcurrencyCount(data.active_sessions);
      }
    } catch (e) {
      console.error("Failed to fetch concurrency", e);
    }
  }, 5000, true);

  return concurrencyCount;
}
