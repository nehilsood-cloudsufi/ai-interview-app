import { useEffect, useRef } from 'react';

/**
 * Passed to every poll callback. `cancelled` flips to true the moment the
 * poll deactivates (unmount, or `active` turning false), so an in-flight
 * async callback can bail out before touching state.
 */
export interface PollSignal {
  cancelled: boolean;
}

/**
 * The one setInterval-polling primitive shared by useConcurrencyPoll,
 * useVendorProfile and useInterviewSummary (each used to hand-roll this).
 *
 * While `active` is true: fires `callback` immediately, then every
 * `intervalMs`. Owns the interval lifecycle - cleared automatically on
 * unmount, on `active` turning false, or on `intervalMs` changing.
 *
 * Deliberately owns NOTHING else: stop conditions (404s, failure budgets,
 * terminal pipeline statuses) differ per consumer and stay in the consumer -
 * a consumer stops itself declaratively by flipping its `active` expression
 * to false. The latest `callback` is kept in a ref, so passing a fresh
 * closure every render neither restarts the interval nor goes stale.
 */
export function useIntervalPoll(
  callback: (signal: PollSignal) => void | Promise<void>,
  intervalMs: number,
  active: boolean,
): void {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });

  useEffect(() => {
    if (!active) return;

    const signal: PollSignal = { cancelled: false };
    const tick = () => {
      if (signal.cancelled) return;
      void callbackRef.current(signal);
    };

    tick();
    const interval = setInterval(tick, intervalMs);
    return () => {
      signal.cancelled = true;
      clearInterval(interval);
    };
  }, [active, intervalMs]);
}
