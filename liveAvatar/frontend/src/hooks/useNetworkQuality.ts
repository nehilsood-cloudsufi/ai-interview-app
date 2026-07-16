import { useEffect, useState } from 'react';
import type { NetworkQuality } from '../types';

export function useNetworkQuality(): NetworkQuality {
  const [networkQuality, setNetworkQuality] = useState<NetworkQuality>('unknown');

  useEffect(() => {
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
