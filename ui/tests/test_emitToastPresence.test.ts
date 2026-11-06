/**
 * @fileoverview Tests how the user's presence changes desktop notifications.
 * Present: toast only. Away: a lasting desktop notification as well. Not marked: never.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const showNotification = vi.fn();
type Presence = 'present' | 'away';
let presence: Presence = 'present';

/** Loads fresh modules, past the start-up grace, with a fake desktop app. */
async function loadReady(desktop = true) {
  vi.resetModules();
  const app: Record<string, unknown> = { showNotification, reportAppFullyLoaded: vi.fn() };
  if (desktop) {
    app.getPresence = () => Promise.resolve(presence);
    app.onPresenceChange = vi.fn(() => () => {});
  }
  (window as unknown as { astrometrics: unknown }).astrometrics = { app };
  const toast = await import('../common/utils/emitToast');
  const boot = await import('../common/utils/appBootReadiness');
  for (const mode of [
    'Image Viewer', 'Astronomy Manager', 'Planetarium', 'Image Processing',
    'Observatory Manager', 'Observation Manager', 'Command Console',
  ]) boot.reportModeReady(mode);
  vi.advanceTimersByTime(toast.NOTIFICATION_STARTUP_GRACE_MS + 1000);
  await Promise.resolve();
  await Promise.resolve();
  return toast;
}

describe('emitToast presence rule', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    showNotification.mockReset();
    presence = 'present';
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete (window as unknown as { astrometrics?: unknown }).astrometrics;
  });

  /** The user is looking, so the toast is enough. */
  it('sends no desktop notification when the user is present', async () => {
    const toast = await loadReady();
    toast.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).not.toHaveBeenCalled();
  });

  /** The user is away, so one lasting notification goes out. */
  it('sends one lasting notification when the user is away', async () => {
    presence = 'away';
    const toast = await loadReady();
    toast.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).toHaveBeenCalledTimes(1);
    expect(showNotification).toHaveBeenCalledWith('Guiding', 'Guiding lost',
      expect.objectContaining({ urgency: 'critical', timeoutType: 'never' }));
  });

  /** Warnings also last until dismissed. */
  it('keeps a warning until dismissed when away', async () => {
    presence = 'away';
    const toast = await loadReady();
    toast.emitToast('Mount stalled', 'warning', 'mount', { notify: true });
    expect(showNotification).toHaveBeenCalledWith('Mount', 'Mount stalled',
      expect.objectContaining({ urgency: 'critical', timeoutType: 'never' }));
  });

  /** A toast that is not marked never reaches the desktop, even when away. */
  it('never notifies for an unmarked toast', async () => {
    presence = 'away';
    const toast = await loadReady();
    toast.emitToast('Stacked', 'success', 'app');
    expect(showNotification).not.toHaveBeenCalled();
  });

  /** In a plain browser, page focus decides. */
  it('falls back to document focus without the desktop app', async () => {
    const toast = await loadReady(false);
    const hasFocus = vi.spyOn(document, 'hasFocus');
    hasFocus.mockReturnValue(true);
    toast.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).not.toHaveBeenCalled();
    hasFocus.mockReturnValue(false);
    toast.emitToast('Guiding lost again', 'error', 'guiding', { notify: true });
    expect(showNotification).toHaveBeenCalledTimes(1);
  });
});
