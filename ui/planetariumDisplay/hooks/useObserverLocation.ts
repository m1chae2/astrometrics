/**
 * @module useObserverLocation
 * @fileoverview React hook for fetching the telescope observer's geographic position.
 *
 * Queries the INDI GPS daemon via the backend API. Falls back to Denver, CO
 * (39.7392°N, 104.9903°W, 1600m) when the backend is unavailable.
 *
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { ObserverLocation } from '../../common/types/planetariumTypes';

/**
 * Fetches the observer's geographic location from the backend INDI GPS daemon.
 *
 * Returns null during the initial load. On error, sets a hard-coded Denver fallback
 * to ensure the sky map renders without a valid GPS fix.
 *
 * @func useObserverLocation
 * @returns {{ location: ObserverLocation | null; loading: boolean }}
 *   location — The observer's coordinates, or null before the fetch completes.
 *   loading — True while the request is in flight.
 */
export const useObserverLocation = () => {
  const [location, setLocation] = useState<ObserverLocation | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let active = true;
    const fetchLocation = async () => {
      try {
        setLoading(true);
        const data = await callBackend('planetarium:get_observer_location', {});
        if (active) {
          setLocation(data);
        }
      } catch (err) {
        console.error('Failed to get observer location, using default', err);
        if (active) {
          // Default: Denver, CO (39.7392°N, 104.9903°W, 1600m ASL)
          setLocation({ latitude: 39.7392, longitude: -104.9903, elevation: 1600.0 });
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchLocation();
    return () => {
      active = false;
    };
  }, []);

  return { location, loading };
};
