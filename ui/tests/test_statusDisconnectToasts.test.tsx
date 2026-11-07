/**
 * @fileoverview Tests which telescope connection changes show a toast.
 * An offline telescope is a status (the status chip shows it), so start-up and idle
 * changes are silent. Only a mount that drops while tracking, after start-up, warns.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const emitToast = vi.fn();
let telescope: Record<string, unknown> = {};
let appReady = false;

vi.mock('../common/utils/emitToast', () => ({ emitToast }));
vi.mock('../common/utils/appBootReadiness', () => ({ isAppReady: () => appReady }));
vi.mock('../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ telescope, connected: true }),
}));

/** Renders the status hook, then feeds it each telescope reading in turn. */
async function feed(readings: Array<Record<string, unknown>>): Promise<void> {
  const { useStatusData } = await import('../statusHeader/hooks/useStatusData');
  telescope = readings[0];
  const { rerender } = renderHook(() => useStatusData());
  for (const reading of readings.slice(1)) {
    telescope = reading;
    rerender();
  }
}

describe('telescope connection toasts', () => {
  beforeEach(() => {
    emitToast.mockReset();
    appReady = false;
  });

  /** Starting with the telescope offline shows nothing. */
  it('shows no toast when the app starts with the telescope offline', async () => {
    await feed([
      { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' },
      { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' },
    ]);
    expect(emitToast).not.toHaveBeenCalled();
  });

  /** A connection that drops while idle, or during start-up, shows nothing. */
  it('shows no toast when an idle telescope disconnects', async () => {
    appReady = true;
    await feed([
      { connectionStatus: 'Connected', trackingStatus: 'Not Tracking' },
      { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' },
    ]);
    expect(emitToast).not.toHaveBeenCalled();
  });

  /** A drop during start-up is never news, even when the telescope was tracking. */
  it('shows no toast for a disconnect before the app is ready', async () => {
    await feed([
      { connectionStatus: 'Connected', trackingStatus: 'Tracking' },
      { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' },
    ]);
    expect(emitToast).not.toHaveBeenCalledWith(expect.stringContaining('disconnected'), expect.anything(), expect.anything(), expect.anything());
  });

  /** A mount that drops while tracking is a warning that asks for attention, never an error. */
  it('warns when a tracking telescope disconnects during a night', async () => {
    appReady = true;
    await feed([
      { connectionStatus: 'Connected', trackingStatus: 'Tracking' },
      { connectionStatus: 'Disconnected', trackingStatus: 'Not Tracking' },
    ]);
    expect(emitToast).toHaveBeenCalledWith(
      'Telescope disconnected while tracking', 'warning', 'telescope', { notify: true }
    );
    expect(emitToast).not.toHaveBeenCalledWith(expect.anything(), 'error', expect.anything());
  });
});
