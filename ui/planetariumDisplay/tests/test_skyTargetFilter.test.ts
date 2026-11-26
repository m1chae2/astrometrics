/**
 * @fileoverview Checks the rules that decide which library targets, and which stack pictures,
 * the Planetarium's sky draws when the target list's filters are on.
 */

import { describe, expect, it } from 'vitest';
import { buildSkyVisibleTargetKeys, filterSkyPictures, isSkyTargetShown } from '../utils/skyTargetFilter';
import type { StackSkyPicture } from '../../common/types/planetariumTypes';

const LIBRARY = ['M 31', 'M 81', 'NGC 7000'];
const LIBRARY_KEYS = new Set(['m 31', 'm 81', 'ngc 7000']);

/**
 * Makes a stack picture for a target.
 * @param targetId The target's name.
 * @returns A picture with no grid.
 */
const picture = (targetId: string): StackSkyPicture => ({
  targetId, picturePath: `/${targetId}.png`, width: 1, height: 1, gridSize: 2, grid: [],
});

describe('buildSkyVisibleTargetKeys', () => {
  /** With both filters off, nothing is filtered. */
  it('returns null when no filter is on', () => {
    expect(buildSkyVisibleTargetKeys(LIBRARY, null, null)).toBeNull();
  });

  /** The Show filter and the horizon check both apply. */
  it('keeps targets that pass the Show filter and are above the horizon', () => {
    const horizon = { checked: new Set(['m 31', 'm 81', 'ngc 7000']), belowHorizon: new Set(['m 31']) };
    const visible = buildSkyVisibleTargetKeys(LIBRARY, ['M 31', 'M 81'], horizon);
    expect(Array.from(visible ?? [])).toEqual(['m 81']);
  });

  /** A target the horizon check could not check is hidden by the horizon filter. */
  it('hides unchecked targets under the horizon filter', () => {
    const horizon = { checked: new Set(['m 81']), belowHorizon: new Set<string>() };
    expect(Array.from(buildSkyVisibleTargetKeys(LIBRARY, null, horizon) ?? [])).toEqual(['m 81']);
  });
});

describe('isSkyTargetShown and filterSkyPictures', () => {
  const visible = new Set(['m 81']);

  /** Hidden library targets drop out; the selected one and non-library markers stay. */
  it('keeps the selected target and markers that are not library targets', () => {
    expect(isSkyTargetShown('M 31', visible, LIBRARY_KEYS, [])).toBe(false);
    expect(isSkyTargetShown('M 31', visible, LIBRARY_KEYS, ['m  31'])).toBe(true);
    expect(isSkyTargetShown('Catalog object', visible, LIBRARY_KEYS, [])).toBe(true);
  });

  /** Pictures of hidden targets are left out, so they are never loaded. */
  it('drops the pictures of hidden targets', () => {
    const kept = filterSkyPictures([picture('M 31'), picture('M 81')], visible, LIBRARY_KEYS, []);
    expect(kept.map((entry) => entry.targetId)).toEqual(['M 81']);
    expect(filterSkyPictures([picture('M 31')], null, LIBRARY_KEYS, [])).toHaveLength(1);
  });
});
