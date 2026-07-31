import { useEffect, useState } from 'react';
import type { SpeakingState } from '../types';

/**
 * Detects a stalled avatar conversation: returns true once the session has
 * sat in a non-speaking state ('idle' or 'processing') for `thresholdMs`
 * continuously while `active`. Speech from either side resets the timer and
 * clears the flag.
 *
 * Exists because HeyGen cancels the avatar's in-flight reply when new user
 * speech fragments arrive, and after the last cancellation it never
 * re-requests one (seen live 2026-07-22): the avatar's next line is silently
 * dropped and both sides wait for the other — a turn-taking deadlock the
 * user can break just by speaking again. The AvatarStallBanner this feeds
 * tells them exactly that.
 */
export function useAvatarStall(active: boolean, speakingState: SpeakingState, thresholdMs = 20000): boolean {
  const [stalled, setStalled] = useState(false);

  useEffect(() => {
    if (!active || speakingState === 'avatar_speaking' || speakingState === 'user_speaking') {
      setStalled(false);
      return;
    }
    const timer = setTimeout(() => setStalled(true), thresholdMs);
    return () => clearTimeout(timer);
  }, [active, speakingState, thresholdMs]);

  return stalled;
}
