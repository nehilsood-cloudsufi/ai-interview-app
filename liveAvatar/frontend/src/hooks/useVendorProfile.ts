import { useCallback, useEffect, useRef, useState } from 'react';
import { API_URL } from '../config';
import type { InterviewStateResponse, UpdateProfileResponse, VendorProfile } from '../types';
import { useIntervalPoll } from './useIntervalPoll';

// Consecutive poll failures (network error, non-ok, 404) before giving up
// quietly.
const MAX_CONSECUTIVE_FAILURES = 3;
const POLL_INTERVAL_MS = 5000;

interface VendorProfileState {
  profile: VendorProfile | null;
  // True once the interview script has reached END - App uses this to
  // auto-stop a still-connected avatar session after the closing line.
  interviewDone: boolean;
  saveProfile: (changes: Partial<VendorProfile>) => Promise<boolean>;
}

/**
 * Polls GET /api/interview/{id}/state for the WHOLE interview (both avatar
 * and chat modes), so a ProfileCard can show - and let the vendor edit via
 * PATCH /api/interview/{id}/profile - the vendor's profile (entered on the
 * start screen's intake form, correctable throughout).
 *
 * Returns `{ profile, interviewDone, saveProfile }`: the profile (null until
 * the first response), whether the script has reached END, and saveProfile —
 * a PATCH of the changed fields that applies the server's echoed profile
 * immediately.
 *
 * Lifecycle: polls every 5s while `active` and an interviewId is set. Stops on
 * a 404 or after a few consecutive failures (giving up quietly); unmount or
 * `active` turning false tears the poll down too.
 */
export function useVendorProfile(interviewId: string | null, active: boolean): VendorProfileState {
  const [profile, setProfile] = useState<VendorProfile | null>(null);
  const [interviewDone, setInterviewDone] = useState(false);

  // The hook's own stop conditions (a 404, or MAX_CONSECUTIVE_FAILURES in a
  // row) flip `gaveUp`, which deactivates the shared poll below. A new
  // interview id (or re-activation) resets both the flag and the counter.
  const [gaveUp, setGaveUp] = useState(false);
  const failuresRef = useRef(0);
  useEffect(() => {
    setGaveUp(false);
    setInterviewDone(false);
    failuresRef.current = 0;
  }, [interviewId, active]);

  const recordFailure = () => {
    failuresRef.current += 1;
    if (failuresRef.current >= MAX_CONSECUTIVE_FAILURES) setGaveUp(true);
  };

  useIntervalPoll(async (signal) => {
    if (!interviewId) return;
    try {
      const res = await fetch(`${API_URL}/api/interview/${interviewId}/state`);
      if (signal.cancelled) return;

      if (!res.ok) {
        if (res.status === 404) {
          setGaveUp(true);
          return;
        }
        recordFailure();
        return;
      }

      failuresRef.current = 0;
      const data: InterviewStateResponse = await res.json();
      if (signal.cancelled) return;

      setProfile(data.vendor_profile);
      setInterviewDone(data.done === true);
    } catch {
      if (signal.cancelled) return;
      recordFailure();
    }
  }, POLL_INTERVAL_MS, active && !!interviewId && !gaveUp);

  // Vendor-initiated manual correction, available for the whole interview.
  // Applies the response's vendor_profile on success so the card reflects
  // the save immediately; a poll landing shortly after just re-confirms the
  // same server state (server is the source of truth), so no versioning is
  // needed to avoid a flicker back to stale values.
  const saveProfile = useCallback(
    async (changes: Partial<VendorProfile>): Promise<boolean> => {
      if (Object.keys(changes).length === 0) return true;
      if (!interviewId) return false;

      try {
        const res = await fetch(`${API_URL}/api/interview/${interviewId}/profile`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(changes),
        });
        if (!res.ok) return false;

        const data: UpdateProfileResponse = await res.json();
        setProfile(data.vendor_profile);
        return true;
      } catch {
        return false;
      }
    },
    [interviewId],
  );

  return { profile, interviewDone, saveProfile };
}
