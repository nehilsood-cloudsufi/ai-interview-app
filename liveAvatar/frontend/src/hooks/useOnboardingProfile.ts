import { useEffect, useState } from 'react';
import { API_URL } from '../config';
import type { InterviewStateResponse, VendorProfile } from '../types';

// Consecutive poll failures (network error, non-ok, 404) before giving up
// quietly. Mirrors useConcurrencyPoll's setInterval + cleanup pattern, but
// this poll must also stop permanently once onboarding is over.
const MAX_CONSECUTIVE_FAILURES = 3;
const POLL_INTERVAL_MS = 5000;

interface OnboardingProfileState {
  profile: VendorProfile | null;
  isOnboarding: boolean;
}

// Polls GET /api/interview/{id}/state while onboarding is in progress, so a
// ProfileCard can show "here's what I captured" during the intro/
// confirm_profile questionnaire nodes. Stops polling permanently once
// current_topic moves past onboarding (or null after END), on a 404, or
// after a few consecutive failures - the card is an onboarding-phase aid,
// not a whole-interview poll.
export function useOnboardingProfile(interviewId: string | null, active: boolean): OnboardingProfileState {
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
            setIsOnboarding(false);
            stop();
            return;
          }
          failures += 1;
          if (failures >= MAX_CONSECUTIVE_FAILURES) {
            setIsOnboarding(false);
            stop();
          }
          return;
        }

        failures = 0;
        const data: InterviewStateResponse = await res.json();
        if (cancelled) return;

        // Keep polling while current_topic is "onboarding" or still null/
        // undefined (not yet resolved); stop once it names any later topic.
        // isOnboarding itself only lights up for the "onboarding" topic.
        const keepPolling = data.current_topic === 'onboarding' || data.current_topic == null;
        setProfile(data.vendor_profile);
        setIsOnboarding(data.current_topic === 'onboarding');
        if (!keepPolling) stop();
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          setIsOnboarding(false);
          stop();
        }
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

  return { profile, isOnboarding };
}
