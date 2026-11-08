/**
 * @fileoverview Tests that useSourceMerging hands the sky map the bundled bright stars
 * even when the deep-star (Gaia) catalog is not installed.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../common/services/backendApi', () => ({
  callBackend: vi.fn(),
}));
vi.mock('../planetariumDisplay/hooks/usePlanetariumSources', () => ({
  usePlanetariumSources: () => ({ sources: [], loading: false, error: null }),
}));
vi.mock('../planetariumDisplay/hooks/usePlanetariumTargets', () => ({
  usePlanetariumTargets: () => ({ targets: [] }),
}));
vi.mock('../planetariumDisplay/hooks/useDeepCatalogStatus', () => ({
  useDeepCatalogStatus: () => ({ status: null }),
}));

import { callBackend } from '../common/services/backendApi';
import { clearCatalogSourceCache } from '../planetariumDisplay/utils/catalogSourceCache';
import { useSourceMerging } from '../planetariumDisplay/hooks/useSourceMerging';

const mockedCallBackend = vi.mocked(callBackend);

/** A bundled bright star as the backend sends it. */
const brightStar = {
  id: 'HIP_32349', ra: 101.3, dec: -16.7, name: 'HIP 32349', magnitude: -1.44, type: 'star',
  hasSpectra: false, hasPhotometry: false, catalogSource: 'hipparcos', hasCatalogMagnitude: true,
};

describe('useSourceMerging without the deep-star catalog', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedCallBackend.mockReset();
    clearCatalogSourceCache();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('still returns the bundled bright stars when the deep-star query finds nothing', async () => {
    mockedCallBackend.mockImplementation((async (_action: string, params: { enabled_drivers: string[] }) => {
      const sources = params.enabled_drivers.includes('hipparcos') ? [brightStar] : [];
      return { sources, starsTruncated: false };
    }) as never);

    const { result } = renderHook(() => useSourceMerging(0, 0, 90, true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(result.current.stars.map(star => star.id)).toEqual(['HIP_32349']);
  });
});
