/**
 * @fileoverview Tests the per-mode "ready" flags that App.tsx uses to decide when
 * a display may be paused (a display is never paused before it has loaded once).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';

describe('mode readiness flags', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('starts not ready, becomes ready when the mode reports, and re-renders watchers', async () => {
    const { useIsModeReady, reportModeReady, isModeReady } = await import('../common/utils/appBootReadiness');
    const { result } = renderHook(() => useIsModeReady('Observation Manager'));
    expect(result.current).toBe(false);
    expect(isModeReady('Planetarium')).toBe(false);

    act(() => reportModeReady('Observation Manager'));
    expect(result.current).toBe(true);
    expect(isModeReady('Planetarium')).toBe(false);
  });

  it('treats every mode as ready once the whole app has reported', async () => {
    const { reportModeReady, isModeReady } = await import('../common/utils/appBootReadiness');
    for (const mode of [
      'Image Viewer', 'Astronomy Manager', 'Planetarium', 'Image Processing',
      'Observatory Manager', 'Observation Manager', 'Command Console',
    ]) {
      reportModeReady(mode);
    }
    expect(isModeReady('Anything')).toBe(true);
  });
});
