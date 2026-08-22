/**
 * @module useConstellationLines
 * @fileoverview React hook for fetching the bundled constellation stick-figure line data.
 *
 * Unlike usePlanetariumSources/useOnlineCatalogSources, this takes no ra/dec/radius —
 * the constellation line dataset is fixed, static, and small (a few hundred segments
 * across all constellations), so it's fetched once and never re-fetched on pan/zoom.
 *
 */

import { useState, useEffect } from 'react';
import { callBackend } from '../../common/services/backendApi';
import { ConstellationLineSegment } from '../../common/types/planetariumTypes';

/**
 * Fetches the full bundled set of constellation stick-figure line segments once.
 *
 * @func useConstellationLines
 * @param {boolean} enabled - When false, returns an empty result set without querying.
 * @returns {{ lines: ConstellationLineSegment[]; loading: boolean; error: string | null }}
 */
export const useConstellationLines = (enabled: boolean) => {
  const [lines, setLines] = useState<ConstellationLineSegment[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Fetch once and keep — the dataset never varies by viewport, so once
    // loaded there's nothing more to re-fetch even as the user pans/zooms.
    if (!enabled || lines.length > 0) return;

    let active = true;
    const fetchLines = async () => {
      try {
        setLoading(true);
        const data = await callBackend('planetarium:get_constellation_lines', {});
        if (active) {
          setLines(data);
          setError(null);
        }
      } catch (error: unknown) {
        if (active) {
          const message = error instanceof Error ? error.message : 'Failed to fetch constellation lines';
          setError(message);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchLines();
    return () => {
      active = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return { lines, loading, error };
};
