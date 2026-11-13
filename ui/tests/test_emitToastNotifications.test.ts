/**
 * @fileoverview Tests when a toast also becomes a desktop notification.
 * Only a toast marked `notify` reaches the desktop, and never during start-up
 * (before the app reports ready, or in the first seconds after the page loads).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const showNotification = vi.fn();

/** Loads fresh copies of the modules so each test starts at "just launched". */
async function loadModules() {
  vi.resetModules();
  const toast = await import('../common/utils/emitToast');
  const boot = await import('../common/utils/appBootReadiness');
  return { ...toast, ...boot };
}

/** Marks every display as loaded, which makes the app "ready". */
function makeAppReady(boot: Awaited<ReturnType<typeof loadModules>>): void {
  for (const mode of [
    'Image Viewer', 'Astronomy Manager', 'Planetarium', 'Image Processing',
    'Observatory Manager', 'Observation Manager', 'Command Console',
  ]) {
    boot.reportModeReady(mode);
  }
}

describe('emitToast desktop notifications', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    showNotification.mockReset();
    (window as unknown as { astrometrics: unknown }).astrometrics = { app: { showNotification, reportAppFullyLoaded: vi.fn() } };
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    // The user is away (the window has no focus). jsdom 31 reports focus by default, so say so.
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    delete (window as unknown as { astrometrics?: unknown }).astrometrics;
  });

  /** An error toast no longer reaches the desktop on its own. */
  it('does not notify for an error toast that is not marked', async () => {
    const m = await loadModules();
    makeAppReady(m);
    vi.advanceTimersByTime(m.NOTIFICATION_STARTUP_GRACE_MS + 1000);
    m.emitToast('Something failed', 'error', 'app');
    expect(showNotification).not.toHaveBeenCalled();
  });

  /** A hidden window no longer turns every toast into a notification. */
  it('does not notify for a toast while the window is hidden', async () => {
    const m = await loadModules();
    makeAppReady(m);
    vi.advanceTimersByTime(m.NOTIFICATION_STARTUP_GRACE_MS + 1000);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    m.emitToast('Stacked', 'success', 'app');
    expect(showNotification).not.toHaveBeenCalled();
  });

  /** A marked toast reaches the desktop once start-up is over. */
  it('notifies for a marked toast after start-up', async () => {
    const m = await loadModules();
    makeAppReady(m);
    vi.advanceTimersByTime(m.NOTIFICATION_STARTUP_GRACE_MS + 1000);
    m.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).toHaveBeenCalledTimes(1);
    expect(showNotification).toHaveBeenCalledWith('Guiding', 'Guiding lost', expect.objectContaining({ urgency: 'critical' }));
  });

  /** Nothing reaches the desktop before the app reports ready. */
  it('does not notify before the app is ready', async () => {
    const m = await loadModules();
    vi.advanceTimersByTime(m.NOTIFICATION_STARTUP_GRACE_MS + 1000);
    m.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).not.toHaveBeenCalled();
  });

  /** Nothing reaches the desktop in the first seconds, even when the app is ready. */
  it('does not notify during the start-up grace period', async () => {
    const m = await loadModules();
    makeAppReady(m);
    m.emitToast('Guiding lost', 'error', 'guiding', { notify: true });
    expect(showNotification).not.toHaveBeenCalled();
  });
});
