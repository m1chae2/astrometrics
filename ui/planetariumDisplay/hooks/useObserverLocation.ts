/**
 * @module useObserverLocation
 * @fileoverview React hook for fetching the telescope observer's geographic position.
 *
 * Queries the INDI GPS daemon via the backend API. Falls back to Denver, CO
 * (39.7392°N, 104.9903°W, 1600m) when the backend is unavailable.
 *
 */

import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { callBackend } from '../../common/services/backendApi';
import { ObserverLocation } from '../../common/types/planetariumTypes';

const DEFAULT_LOCATION: ObserverLocation = { latitude: 39.7392, longitude: -104.9903, elevation: 1600.0 };

/**
 * Fetches the observer's geographic location from the backend INDI GPS daemon.
 *
 * Returns the Denver fallback during the initial load and if the fetch fails,
 * so the sky map always has a location to render with.
 *
 * @func useObserverLocation
 * @returns {{ location: ObserverLocation; loading: boolean }}
 *   location — The observer's coordinates, or the Denver fallback before the fetch completes or on failure.
 *   loading — True while the request is in flight.
 */
export const useObserverLocation = () => {
  const { data, loading } = useBackendFetch<ObserverLocation>(
    async (signal) => {
      try {
        return await callBackend('planetarium:get_observer_location', {}, { signal, silent: true });
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          throw err;
        }
        console.error('Failed to get observer location, using default', err);
        return DEFAULT_LOCATION;
      }
    },
    []
  );

  return { location: data ?? DEFAULT_LOCATION, loading };
};
