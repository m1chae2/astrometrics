/**
 * @file test_modePanelPause.test.tsx
 * @description Tests how App.tsx pauses hidden displays: a display is never paused
 * before it has reported ready (so the splash screen can finish), a ready hidden
 * display is paused (its effects clean up) and starts again when shown, and the
 * displays that cannot be paused safely stay running.
 */

import React, { useEffect } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';

vi.mock('../common/context/TargetContext', () => ({
  TargetProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DeferWhileHidden: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../common/context/AstrometricsContext', () => ({
  AstrometricsProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAstrometrics: () => ({ config: null }),
}));

/** Records when its effects start and stop. */
function makeProbe(log: string[]): React.FC {
  return function Probe() {
    useEffect(() => {
      log.push('start');
      return () => {
        log.push('stop');
      };
    }, []);
    return null;
  };
}

describe('ModePanel pausing', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  /** A display that has not loaded yet is never paused, even when hidden. */
  it('does not pause a hidden display before it reported ready', async () => {
    const { ModePanel } = await import('../App');
    const log: string[] = [];
    const Probe = makeProbe(log);
    render(
      <ModePanel panelMode="Planetarium" activeMode="Image Viewer">
        <Probe />
      </ModePanel>
    );
    expect(log).toEqual(['start']);
  });

  /** After it reported ready, a hidden pausable display stops its effects and restarts them when shown. */
  it('pauses a ready hidden display and restarts it when shown', async () => {
    const { ModePanel } = await import('../App');
    const { reportModeReady } = await import('../common/utils/appBootReadiness');
    const log: string[] = [];
    const Probe = makeProbe(log);
    const { rerender } = render(
      <ModePanel panelMode="Planetarium" activeMode="Image Viewer">
        <Probe />
      </ModePanel>
    );
    act(() => reportModeReady('Planetarium'));
    expect(log).toEqual(['start', 'stop']);

    rerender(
      <ModePanel panelMode="Planetarium" activeMode="Planetarium">
        <Probe />
      </ModePanel>
    );
    expect(log).toEqual(['start', 'stop', 'start']);
  });

  /** The splash screen finishes once every display reported, with the hidden ones paused. */
  it('lets the splash complete: every display reports, then the app is fully loaded', async () => {
    const reportAppFullyLoaded = vi.fn();
    (window as unknown as { astrometrics: unknown }).astrometrics = { app: { reportAppFullyLoaded } };
    const { reportModeReady } = await import('../common/utils/appBootReadiness');
    for (const mode of [
      'Image Viewer', 'Astronomy Manager', 'Planetarium', 'Image Processing',
      'Observatory Manager', 'Observation Manager', 'Command Console',
    ]) {
      reportModeReady(mode);
    }
    expect(reportAppFullyLoaded).toHaveBeenCalledTimes(1);
    delete (window as unknown as { astrometrics?: unknown }).astrometrics;
  });

  /** Every display, Image Processing included, may pause (see the note on PAUSABLE_MODES). */
  it('lists the pausable displays', async () => {
    const { PAUSABLE_MODES } = await import('../App');
    expect([...PAUSABLE_MODES].sort()).toEqual([
      'Astronomy Manager', 'Command Console', 'Image Processing', 'Image Viewer',
      'Observation Manager', 'Observatory Manager', 'Planetarium',
    ]);
  });
});
