import { useCallback, useEffect, useState } from 'react';
import { API_URL } from '../config';
import type { InterviewStateResponse, UpdateProfileResponse, VendorProfile } from '../types';

// Consecutive poll failures (network error, non-ok, 404) before giving up
// quietly. Mirrors useConcurrencyPoll's setInterval + cleanup pattern.
const MAX_CONSECUTIVE_FAILURES = 3;
const POLL_INTERVAL_MS = 5000;

interface VendorProfileState {
  profile: VendorProfile | null;
  isOnboarding: boolean;
  saveProfile: (changes: Partial<VendorProfile>) => Promise<boolean>;
}

// Polls GET /api/interview/{id}/state for the WHOLE interview (both avatar
// and chat modes), so a ProfileCard can show - and let the vendor edit via
// PATCH /api/interview/{id}/profile - the profile Noor has captured, not
// just during onboarding. Stops only on a 404 or after a few consecutive
// failures; unmount / active=false tears the poll down too. isOnboarding
// still reflects current_topic === 'onboarding' so the card's heading can
// change once onboarding wraps up, but (unlike the old onboarding-only hook)
// the poll itself keeps running past that point.
export function useVendorProfile(interviewId: string | null, active: boolean): VendorProfileState {
  const [profile, setProfile] = useState<VendorProfile | null>(null);
  const [isOnboarding, setIsOnboarding] = useState(false);

  useEffect(() => {
    if (!active || !interviewId) return;

    let cancelled = false;
    let failures = 0;

    const poll = async () => {
      try {
        const res = await fetch(`${API_URL}/api/interview/${interviewId}/state`);
        if (cancelled) return;

        if (!res.ok) {
          if (res.status === 404) {
            stop();
            return;
          }
          failures += 1;
          if (failures >= MAX_CONSECUTIVE_FAILURES) stop();
          return;
        }

        failures = 0;
        const data: InterviewStateResponse = await res.json();
        if (cancelled) return;

        setProfile(data.vendor_profile);
        setIsOnboarding(data.current_topic === 'onboarding');
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures >= MAX_CONSECUTIVE_FAILURES) stop();
      }
    };

    const stop = () => {
      cancelled = true;
      clearInterval(interval);
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [interviewId, active]);

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

  return { profile, isOnboarding, saveProfile };
}
