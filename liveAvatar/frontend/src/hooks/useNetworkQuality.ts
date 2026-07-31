import { useEffect, useState } from 'react';
import type { NetworkQuality } from '../types';

/**
 * Reports a coarse connection-quality bucket ('excellent' | 'good' | 'poor' |
 * 'unknown') derived from the browser's Network Information API (rtt +
 * downlink). App.tsx uses 'poor' to auto-suggest the avatar→text-chat switch.
 *
 * Returns the current bucket (starts 'unknown'). On mount it reads
 * navigator.connection and subscribes to its 'change' event, re-deriving the
 * bucket on each change; the listener is removed on unmount. Where the API is
 * unavailable (e.g. Firefox/Safari) the value stays 'unknown' and nothing is
 * subscribed.
 */
export function useNetworkQuality(): NetworkQuality {
  const [networkQuality, setNetworkQuality] = useState<NetworkQuality>('unknown');

  useEffect(() => {
    // The Network Information API isn't in the TS DOM lib and the vendor-
    // prefixed variants aren't typed at all, so accessing it trips the
    // compiler; @ts-ignore is the pragmatic escape here.
    // @ts-ignore
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (conn) {
      const updateQuality = () => {
        if (conn.rtt === 0) setNetworkQuality('unknown');
        else if (conn.rtt < 100 && conn.downlink > 2) setNetworkQuality('excellent');
        else if (conn.rtt < 300 && conn.downlink > 1) setNetworkQuality('good');
        else setNetworkQuality('poor');
      };
      updateQuality();
      conn.addEventListener('change', updateQuality);
      return () => conn.removeEventListener('change', updateQuality);
    }
  }, []);

  return networkQuality;
}
