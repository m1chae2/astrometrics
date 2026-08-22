/**
 * @fileoverview Unit test suite for shared Planetarium overlay helpers.
 *
 * Covers the horizon-occlusion rule shared by EnvironmentOverlay, StarFieldRenderer,
 * and hitTesting — previously hitTesting reimplemented an incomplete version of
 * this rule that ignored whether the viewport itself dips below the horizon.
 */

import { describe, it, expect } from 'vitest';
import { viewportDipsBelowHorizon, shouldOccludeBelowHorizon } from '../planetariumDisplay/layers/overlayShared';

describe('viewportDipsBelowHorizon', () => {
  it('returns true when the viewport lower edge is below the horizon', () => {
    expect(viewportDipsBelowHorizon(10, 30)).toBe(true); // 10 - 15 = -5
  });

  it('returns false when the whole viewport is above the horizon', () => {
    expect(viewportDipsBelowHorizon(45, 30)).toBe(false); // 45 - 15 = 30
  });
});

describe('shouldOccludeBelowHorizon', () => {
  it('occludes a below-horizon source when the viewport dips below the horizon and environment is shown', () => {
    expect(shouldOccludeBelowHorizon(true, 10, 30, -5)).toBe(true);
  });

  it('does not occlude when the whole viewport is above the horizon, even if the source alt is negative', () => {
    // Regression: a viewport entirely above the horizon renders no ground overlay,
    // so a marginal below-horizon source at the FOV edge is still visibly rendered
    // and must remain selectable.
    expect(shouldOccludeBelowHorizon(true, 45, 30, -1)).toBe(false);
  });

  it('does not occlude when the environment overlay is off', () => {
    expect(shouldOccludeBelowHorizon(false, 10, 30, -5)).toBe(false);
  });

  it('does not occlude a source that is above the horizon', () => {
    expect(shouldOccludeBelowHorizon(true, 10, 30, 5)).toBe(false);
  });
});
