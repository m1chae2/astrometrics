/**
 * @file useStarIdentity.ts
 * @description Loads what the library knows about one named star (its catalog magnitude, spectral type and
 * stored findings), for the identity line of the star inspector. It looks the star up by its catalog keys
 * (Gaia DR3 id, then name) once for each star and ignores an answer that arrives after another star was
 * chosen. A star with no record, or a failed call, gives null: the inspector then shows only the name.
 */
import { useEffect, useState } from 'react';
import { findCatalogRecord, type AstrometryOverlayStar } from '../../services/astronomyService';
import type { Spectrum } from '../../types/backendTypes';

/**
 * Loads the record of one star.
 *
 * @param star The overlay star, or null when the measured star has no catalog match.
 * @returns The record, or null while it loads, when there is none or when the call failed.
 */
export function useStarIdentity(star: Pick<AstrometryOverlayStar, 'name' | 'gaiaSourceId'> | null): Spectrum | null {
  const [record, setRecord] = useState<{ key: string; star: Spectrum | null } | null>(null);
  const key = star ? `${star.gaiaSourceId ?? ''}|${star.name}` : null;
  const gaiaSourceId = star?.gaiaSourceId;
  const name = star?.name;

  useEffect(() => {
    if (!key || name === undefined) return;
    const controller = new AbortController();
    findCatalogRecord({ name, gaiaSourceId }, controller.signal)
      .then((found) => { if (!controller.signal.aborted) setRecord({ key, star: found }); })
      .catch(() => { if (!controller.signal.aborted) setRecord({ key, star: null }); });
    return () => { controller.abort(); };
  }, [key, name, gaiaSourceId]);

  return key && record?.key === key ? record.star : null;
}
