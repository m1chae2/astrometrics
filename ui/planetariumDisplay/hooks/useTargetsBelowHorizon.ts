/**
 * @module useTargetsBelowHorizon
 * @fileoverview Works out which library targets are under the horizon at the
 * Planetarium's planning time, for the "Below" badge and the "Only targets
 * above the horizon" filter in the target list.
 */

import { useEffect, useMemo, useState } from 'react';
import type { ObserverLocation, PlanetariumTarget } from '../../common/types/planetariumTypes';
import { calculateLST } from '../utils/siderealTime';
import { getAltAz } from '../utils/projectionMath';

/** How often, in milliseconds, the live (Now) check runs again. Targets move slowly, so once a minute is enough. */
const LIVE_REFRESH_INTERVAL_MS = 60_000;

/**
 * Turns a target name into a key for matching, so "M 31", "m 31" and "M  31 "
 * all match. The target list and the sky map's target list come from two
 * different requests, so their names may differ in case or spacing.
 *
 * @param {string} name - A target name.
 * @returns {string} The name in lower case with runs of spaces made single and the ends trimmed.
 */
export const normalizeTargetName = (name: string): string => name.trim().replace(/\s+/g, ' ').toLowerCase();

/**
 * The result of a horizon check: which targets are under the horizon, and
 * which ones could be checked at all (a target with no coordinates cannot).
 */
export interface HorizonCheck {
  /** Normalized names (see {@link normalizeTargetName}) of targets at or under the horizon. */
  belowHorizon: Set<string>;
  /** Normalized names of every target that had coordinates and was checked. */
  checked: Set<string>;
}

/**
 * Finds the targets that are at or under the horizon (altitude of 0 degrees
 * or less) for an observer at one moment. This is a pure function, so it can
 * be tested without React.
 *
 * @param {PlanetariumTarget[]} targets - Targets with Right Ascension and Declination in degrees.
 * @param {number} latitude - Observer latitude in degrees.
 * @param {number} longitude - Observer longitude in degrees (negative = west).
 * @param {Date} moment - The time to check.
 * @returns {HorizonCheck} The targets under the horizon and the targets that were checked.
 */
export const findTargetsBelowHorizon = (
  targets: PlanetariumTarget[],
  latitude: number,
  longitude: number,
  moment: Date,
): HorizonCheck => {
  const localSiderealTime = calculateLST(moment, 0, longitude);
  const belowHorizon = new Set<string>();
  const checked = new Set<string>();
  for (const target of targets) {
    if (!Number.isFinite(target.ra) || !Number.isFinite(target.dec)) continue;
    const key = normalizeTargetName(target.id);
    checked.add(key);
    const { alt } = getAltAz(target.ra, target.dec, localSiderealTime, latitude);
    if (alt <= 0) belowHorizon.add(key);
  }
  return { belowHorizon, checked };
};

/**
 * Keeps the horizon check up to date for the Planetarium's target list.
 *
 * With a fixed planning time, it checks once for that time. In live (Now)
 * mode, it checks again about once a minute.
 *
 * @param {PlanetariumTarget[]} targets - The library targets with coordinates.
 * @param {ObserverLocation | null | undefined} location - The observer's site. With no site the result is null.
 * @param {boolean} isLocationReady - False while the site is still loading. Then the result is null.
 * @param {string | null} planningTime - The planning time (a date string), or null for Now.
 * @returns {HorizonCheck | null} The check, or null when the site is not known yet.
 */
export const useTargetsBelowHorizon = (
  targets: PlanetariumTarget[],
  location: ObserverLocation | null | undefined,
  isLocationReady: boolean,
  planningTime: string | null,
): HorizonCheck | null => {
  // Bumped once a minute in live mode so the check runs again with the new clock time.
  const [liveTick, setLiveTick] = useState<number>(0);

  useEffect(() => {
    if (planningTime !== null) return undefined;
    const intervalId = window.setInterval(() => setLiveTick((tick) => tick + 1), LIVE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [planningTime]);

  const latitude = location?.latitude;
  const longitude = location?.longitude;

  return useMemo(() => {
    if (!isLocationReady || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
    const moment = planningTime === null ? new Date() : new Date(planningTime);
    return findTargetsBelowHorizon(targets, latitude as number, longitude as number, moment);
    // liveTick is in the list only so live mode checks again each minute.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targets, latitude, longitude, isLocationReady, planningTime, liveTick]);
};
