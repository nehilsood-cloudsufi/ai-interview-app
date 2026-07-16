import { useEffect, useState } from 'react';
import { API_URL } from '../config';

export function useConcurrencyPoll(): number | null {
  const [concurrencyCount, setConcurrencyCount] = useState<number | null>(null);

  useEffect(() => {
    const fetchConcurrency = async () => {
      try {
        const res = await fetch(`${API_URL}/api/concurrency`);
        if (res.ok) {
          const data = await res.json();
          setConcurrencyCount(data.active_sessions);
        }
      } catch (e) {
        console.error("Failed to fetch concurrency", e);
      }
    };
    fetchConcurrency();
    const interval = setInterval(fetchConcurrency, 5000);
    return () => clearInterval(interval);
  }, []);

  return concurrencyCount;
}
