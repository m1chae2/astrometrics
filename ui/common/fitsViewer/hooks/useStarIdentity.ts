/**
 * @file useStarIdentity.ts
 * @description Loads what the library knows about one named star (its catalog magnitude, spectral type and
 * stored findings), for the identity line of the star inspector. It asks `astronomy:get` once for each star
 * id and ignores an answer that arrives after another star was chosen. A star with no record, or a failed
 * call, gives null: the inspector then shows only the name.
 */
import { useEffect, useState } from 'react';
import { callBackend } from '../../services/backendApi';
import type { Spectrum } from '../../types/backendTypes';

/**
 * Loads the record of one star.
 *
 * @param starId The star's id, or null when the measured star has no catalog match.
 * @returns The record, or null while it loads, when there is none or when the call failed.
 */
export function useStarIdentity(starId: string | null): Spectrum | null {
  const [record, setRecord] = useState<{ id: string; star: Spectrum | null } | null>(null);

  useEffect(() => {
    if (!starId) return;
    let cancelled = false;
    callBackend('astronomy:get', { star_id: starId })
      .then((star) => { if (!cancelled) setRecord({ id: starId, star: star ?? null }); })
      .catch(() => { if (!cancelled) setRecord({ id: starId, star: null }); });
    return () => { cancelled = true; };
  }, [starId]);

  return starId && record?.id === starId ? record.star : null;
}
