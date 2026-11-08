/**
 * @file test_displayCoordinator.test.ts
 * @description Unit tests for the universal cross-display navigation coordinator.
 * Verifies remote intent routing via Electron IPC, fallback to local workspace
 * mode changes, and the hand-off the receiving display reads.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { navigateToElement, NavigationIntent } from '../utils/displayCoordinator';
import { peekHandoffs } from '../utils/displayHandoffStore';

describe('displayCoordinator', () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete (window as any).astrometrics;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * Verifies that when Electron routeDisplayAction succeeds remotely,
   * the local window does not dispatch a modeChange event.
   */
  it('delegates navigation to remote window when target display is already open', async () => {
    const routeDisplayActionMock = vi.fn().mockResolvedValue({ handledRemotely: true, targetWindowId: 2 });
    (window as any).astrometrics = {
      app: {
        routeDisplayAction: routeDisplayActionMock,
      },
    };

    const modeChangeSpy = vi.fn();
    window.addEventListener('astrometrics:modeChange', modeChangeSpy);

    const intent: NavigationIntent = {
      targetDisplay: 'Image Processing',
      targetElement: 'fitsViewer',
      action: 'targetSelected',
      payload: { targetId: 'M 13', starId: 'test-star' },
      toast: { message: 'Opened M 13 remotely' },
    };

    const result = await navigateToElement(intent);

    expect(result.handledRemotely).toBe(true);
    expect(routeDisplayActionMock).toHaveBeenCalledWith(intent);
    expect(modeChangeSpy).not.toHaveBeenCalled();

    window.removeEventListener('astrometrics:modeChange', modeChangeSpy);
  });

  /**
   * Verifies that when Electron routeDisplayAction reports the target display is not open elsewhere,
   * the coordinator falls back to switching the local window display and dispatching the action.
   */
  it('falls back to local display switch when target display is not open on another window', async () => {
    const routeDisplayActionMock = vi.fn().mockResolvedValue({ handledRemotely: false });
    (window as any).astrometrics = {
      app: {
        routeDisplayAction: routeDisplayActionMock,
      },
    };

    const modeChangeSpy = vi.fn();
    window.addEventListener('astrometrics:modeChange', modeChangeSpy);

    const intent: NavigationIntent = {
      targetDisplay: 'Astronomy Manager',
      targetElement: 'spectrumViewer',
      action: 'astronomySelectStar',
      payload: 'HD 151023',
    };

    const result = await navigateToElement(intent);

    expect(result.handledRemotely).toBe(false);
    expect(routeDisplayActionMock).toHaveBeenCalledWith(intent);
    expect(modeChangeSpy).toHaveBeenCalled();
    expect((modeChangeSpy.mock.calls[0][0] as CustomEvent).detail).toBe('Astronomy Manager');
    // The request waits in the hand-off store for the Astronomy Manager.
    const waiting = peekHandoffs('Astronomy Manager', 'astronomySelectStar');
    expect(waiting).toHaveLength(1);
    expect(waiting[0].payload).toBe('HD 151023');
    expect(waiting[0].element).toBe('spectrumViewer');
    expect(window.localStorage.getItem('appMode')).toBe('Astronomy Manager');

    window.removeEventListener('astrometrics:modeChange', modeChangeSpy);
  });

  /**
   * Verifies that when window.astrometrics is absent, local navigation is executed.
   */
  it('falls back to local navigation when running outside Electron', async () => {
    const modeChangeSpy = vi.fn();
    window.addEventListener('astrometrics:modeChange', modeChangeSpy);

    const intent: NavigationIntent = {
      targetDisplay: 'Planetarium',
      action: 'planetariumLocateStar',
      payload: { ra: 180, dec: 45 },
    };

    const result = await navigateToElement(intent);

    expect(result.handledRemotely).toBe(false);
    expect(modeChangeSpy).toHaveBeenCalled();
    expect((modeChangeSpy.mock.calls[0][0] as CustomEvent).detail).toBe('Planetarium');

    window.removeEventListener('astrometrics:modeChange', modeChangeSpy);
  });

  /** Switching the display in this window is its own feedback: the intent's toast is not shown. */
  it('shows no toast when it switches the display in this window', async () => {
    const toasts: unknown[] = [];
    const listener = (event: Event) => toasts.push((event as CustomEvent).detail);
    window.addEventListener('astrometrics:toast', listener);
    (window as any).astrometrics = { app: { routeDisplayAction: vi.fn().mockResolvedValue({ handledRemotely: false }) } };
    await navigateToElement({
      targetDisplay: 'Astronomy Manager',
      action: 'astronomySelectStar',
      payload: 'HD 1',
      toast: { message: 'Opening HD 1 in Astronomy Manager' },
    });
    window.removeEventListener('astrometrics:toast', listener);
    expect(toasts).toHaveLength(0);
  });

  /** When another window handled it, the toast tells the user where it went. */
  it('shows the toast when another window handled the request', async () => {
    const toasts: unknown[] = [];
    const listener = (event: Event) => toasts.push((event as CustomEvent).detail);
    window.addEventListener('astrometrics:toast', listener);
    (window as any).astrometrics = { app: { routeDisplayAction: vi.fn().mockResolvedValue({ handledRemotely: true }) } };
    await navigateToElement({
      targetDisplay: 'Astronomy Manager',
      action: 'astronomySelectStar',
      payload: 'HD 2',
      toast: { message: 'Opening HD 2 in Astronomy Manager' },
    });
    window.removeEventListener('astrometrics:toast', listener);
    expect(toasts).toHaveLength(1);
  });
});
