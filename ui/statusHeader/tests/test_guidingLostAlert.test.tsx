/**
 * @fileoverview Tests for the "Guiding lost" alert in the status header hook:
 * it fires when fresh guiding samples stop while a capture is in progress,
 * asks for a desktop notification, and stays quiet at start-up.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const emitToast = vi.fn();
let appReady = true;
let telescope: Record<string, unknown> = {};

vi.mock('../../common/utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToast(...args) }));
vi.mock('../../common/utils/appBootReadiness', () => ({ isAppReady: () => appReady }));
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ telescope, connected: true }),
}));

import { useStatusData } from '../hooks/useStatusData';

/** A guiding history whose newest sample has the given age in seconds. */
function history(ageSeconds: number): Record<string, unknown>[] {
  return [{ time: Date.now() / 1000 - ageSeconds, rmsRa: 0.5, rmsDec: 0.4 }];
}

describe('guiding lost alert', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    emitToast.mockReset();
    appReady = true;
    telescope = { connectionStatus: 'Connected', trackingStatus: 'Tracking', cameraStatus: 'Exposing (30s)', guidingHistory: history(1) };
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Lets the staleness timer (every 5 s) notice that the samples went old. */
  function goStale(rerender: () => void): void {
    telescope = { ...telescope, guidingHistory: history(120) };
    act(() => { vi.advanceTimersByTime(5000); });
    rerender();
  }

  /** Samples that stop arriving raise a warning that also asks for a desktop notification. */
  it('warns once, with a notification, when guiding stops during an exposure', () => {
    const { rerender } = renderHook(() => useStatusData());
    goStale(rerender);
    const lost = emitToast.mock.calls.filter((call) => String(call[0]).startsWith('Guiding lost'));
    expect(lost).toHaveLength(1);
    expect(lost[0][1]).toBe('warning');
    expect(lost[0][3]).toEqual({ notify: true });
  });

  /** A guiding stop between captures (camera idle) may be intended, so there is no alert. */
  it('stays quiet when guiding stops while the camera is idle', () => {
    telescope = { ...telescope, cameraStatus: 'Idle' };
    const { rerender } = renderHook(() => useStatusData());
    goStale(rerender);
    expect(emitToast.mock.calls.some((call) => String(call[0]).startsWith('Guiding lost'))).toBe(false);
  });

  /** Before the app is ready nothing is announced. */
  it('stays quiet before the app is ready', () => {
    appReady = false;
    const { rerender } = renderHook(() => useStatusData());
    goStale(rerender);
    expect(emitToast.mock.calls.some((call) => String(call[0]).startsWith('Guiding lost'))).toBe(false);
  });

  /** Never guiding means nothing was lost. */
  it('stays quiet when guiding never ran', () => {
    telescope = { connectionStatus: 'Connected', trackingStatus: 'Tracking', guidingHistory: [] };
    const { rerender } = renderHook(() => useStatusData());
    act(() => { vi.advanceTimersByTime(10000); });
    rerender();
    expect(emitToast.mock.calls.some((call) => String(call[0]).startsWith('Guiding lost'))).toBe(false);
  });
});
