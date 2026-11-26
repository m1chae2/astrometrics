/**
 * @fileoverview Checks the horizon test behind the Planetarium target list's
 * "Below" badge and its "Only targets above the horizon" filter.
 */

import { describe, expect, it } from 'vitest';
import type { PlanetariumTarget } from '../../common/types/planetariumTypes';
import { findTargetsBelowHorizon, normalizeTargetName } from '../hooks/useTargetsBelowHorizon';
import { calculateLST } from '../utils/siderealTime';

const LATITUDE = 45.68;
const LONGITUDE = -111.04;
const MOMENT = new Date(Date.UTC(2026, 10, 25, 5, 0, 0));
const LOCAL_SIDEREAL_TIME = calculateLST(MOMENT, 0, LONGITUDE);

describe('findTargetsBelowHorizon', () => {
  /** A target on the meridian is up, and one on the opposite side of the sky is down. */
  it('marks a target below the horizon and leaves an overhead target unmarked', () => {
    const targets: PlanetariumTarget[] = [
      { id: 'Overhead', ra: LOCAL_SIDEREAL_TIME, dec: LATITUDE },
      { id: 'Underfoot', ra: (LOCAL_SIDEREAL_TIME + 180) % 360, dec: -LATITUDE },
    ];
    const result = findTargetsBelowHorizon(targets, LATITUDE, LONGITUDE, MOMENT);
    expect(result.belowHorizon.has('underfoot')).toBe(true);
    expect(result.belowHorizon.has('overhead')).toBe(false);
    expect(result.checked.size).toBe(2);
  });

  /** A star near the north pole never sets from this site, and a far southern one never rises. */
  it('keeps a circumpolar target up and a far southern target down at every hour', () => {
    const targets: PlanetariumTarget[] = [
      { id: 'Polaris', ra: 37.95, dec: 89.26 },
      { id: 'South', ra: 37.95, dec: -80 },
    ];
    for (let hour = 0; hour < 24; hour += 1) {
      const moment = new Date(MOMENT.getTime() + hour * 3_600_000);
      const result = findTargetsBelowHorizon(targets, LATITUDE, LONGITUDE, moment);
      expect(result.belowHorizon.has('polaris')).toBe(false);
      expect(result.belowHorizon.has('south')).toBe(true);
    }
  });

  /** A target with no usable coordinates is not checked, so it gets no badge. */
  it('skips targets without coordinates', () => {
    const targets = [{ id: 'No position', ra: Number.NaN, dec: 10 }] as PlanetariumTarget[];
    const result = findTargetsBelowHorizon(targets, LATITUDE, LONGITUDE, MOMENT);
    expect(result.checked.size).toBe(0);
    expect(result.belowHorizon.size).toBe(0);
  });
});

describe('normalizeTargetName', () => {
  /** Names that differ only in case or spacing give the same key. */
  it('ignores case and extra spaces', () => {
    expect(normalizeTargetName('  M  31 ')).toBe(normalizeTargetName('m 31'));
  });
});
