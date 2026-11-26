/**
 * @file useSelectedSourceVisibility.ts
 * @description Asks the backend where the selected sky object is at the planning time: its
 * altitude and azimuth, its rise, transit and set times, and whether the mount must flip at the
 * meridian. The answer is added to the selected object, so its card can show it.
 */

import React, { useEffect, useState } from 'react';
import { callBackend } from '../../common/services/backendApi';
import type { PlanetariumSource } from '../../common/types/planetariumTypes';

// How often, in milliseconds, the position is asked for again while the planning time is Now.
const LIVE_REFRESH_MS = 10000;

/**
 * Keeps the selected object's position and rise and set times up to date.
 *
 * A chosen planning time gives the same answer every time, so the backend is asked once. While
 * the planning time is Now, it is asked again every 10 seconds. An alignment point is not asked
 * about.
 *
 * @param selectedSource The selected object, or null.
 * @param setSelectedSource Updates the selected object; the answer is merged into it.
 * @param targetTimeIso The planning time as an ISO string, or undefined for Now.
 * @param isLiveTime True when the planning time is Now.
 * @returns The id of the object whose answer is still awaited, or null. Its card shows a loading
 * bar for just those times until they come.
 */
export const useSelectedSourceVisibility = (
  selectedSource: PlanetariumSource | null,
  setSelectedSource: React.Dispatch<React.SetStateAction<PlanetariumSource | null>>,
  targetTimeIso: string | undefined,
  isLiveTime: boolean,
): string | null => {
  const [visibilityPendingId, setVisibilityPendingId] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedSource || selectedSource.type === 'alignment') return;
    let active = true;
    const controller = new AbortController();
    setVisibilityPendingId(selectedSource.id);

    const fetchDetails = async () => {
      try {
        const details = await callBackend(
          'planetarium:get_visibility',
          {
            // The position lets the backend place a background catalog star the library does not hold.
            objects: [{ id: selectedSource.id, type: selectedSource.type || 'star', ra: selectedSource.ra, dec: selectedSource.dec }],
            time: targetTimeIso || new Date().toISOString()
          },
          { signal: controller.signal }
        );
        if (active && details && details.length > 0) {
          setSelectedSource(prev => {
            if (!prev || prev.id !== selectedSource.id) return prev;
            return {
              ...prev,
              ra: prev.ra,
              dec: prev.dec,
              altitude: details[0].altitude_deg,
              azimuth: details[0].azimuth_deg,
              hourAngle: details[0].meridian?.hour_angle_hours,
              flipRequired: details[0].meridian?.flip_required,
              timeToFlipSeconds: details[0].meridian?.time_to_flip_seconds,
              riseTime: details[0].rise_utc,
              setTime: details[0].set_utc,
              transitTime: details[0].transit_utc,
              aboveHorizon: details[0].above_horizon
            };
          });
        }
      } catch (error: any) {
        if (error?.name !== 'AbortError') {
          console.error("Failed to fetch detailed visibility status", error);
        }
      } finally {
        if (active) setVisibilityPendingId((pending) => (pending === selectedSource.id ? null : pending));
      }
    };

    fetchDetails();
    // A chosen moment gives the same answer every time, so only live time is asked again.
    const interval = isLiveTime ? setInterval(fetchDetails, LIVE_REFRESH_MS) : undefined;
    return () => {
      active = false;
      controller.abort();
      if (interval !== undefined) clearInterval(interval);
    };
    // Only a new object or a new time asks again; the object's own updates (this answer) do not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSource?.id, targetTimeIso, isLiveTime]);

  return visibilityPendingId;
};
