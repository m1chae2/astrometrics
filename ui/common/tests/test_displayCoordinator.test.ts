/**
 * @file test_displayCoordinator.test.ts
 * @description Unit tests for the universal cross-display navigation coordinator.
 * Verifies remote intent routing via Electron IPC, fallback to local workspace
 * mode changes, and receiver hook filtering.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { navigateToElement, useNavigationTarget, NavigationIntent } from '../utils/displayCoordinator';

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
    const actionSpy = vi.fn();
    const navigationIntentSpy = vi.fn();

    window.addEventListener('astrometrics:modeChange', modeChangeSpy);
    window.addEventListener('astrometrics:astronomySelectStar', actionSpy);
    window.addEventListener('astrometrics:navigationIntent', navigationIntentSpy);

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
    expect(actionSpy).toHaveBeenCalled();
    expect((actionSpy.mock.calls[0][0] as CustomEvent).detail).toBe('HD 151023');
    expect(navigationIntentSpy).toHaveBeenCalled();
    expect(window.localStorage.getItem('appMode')).toBe('Astronomy Manager');

    window.removeEventListener('astrometrics:modeChange', modeChangeSpy);
    window.removeEventListener('astrometrics:astronomySelectStar', actionSpy);
    window.removeEventListener('astrometrics:navigationIntent', navigationIntentSpy);
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

  /**
   * Verifies that useNavigationTarget receives matching intents and ignores non-matching intents.
   */
  it('useNavigationTarget invokes callback only for matching display and elementId', () => {
    const onIntentMock = vi.fn();

    renderHook(() => useNavigationTarget('Image Processing', 'fitsViewer', onIntentMock));

    // Non-matching display
    window.dispatchEvent(
      new CustomEvent('astrometrics:navigationIntent', {
        detail: { targetDisplay: 'Planetarium', targetElement: 'fitsViewer', action: 'test', payload: {} },
      })
    );
    expect(onIntentMock).not.toHaveBeenCalled();

    // Non-matching element
    window.dispatchEvent(
      new CustomEvent('astrometrics:navigationIntent', {
        detail: { targetDisplay: 'Image Processing', targetElement: 'frameList', action: 'test', payload: {} },
      })
    );
    expect(onIntentMock).not.toHaveBeenCalled();

    // Matching intent
    const matchingIntent: NavigationIntent = {
      targetDisplay: 'Image Processing',
      targetElement: 'fitsViewer',
      action: 'selectFrame',
      payload: { frameIndex: 3 },
    };
    window.dispatchEvent(
      new CustomEvent('astrometrics:navigationIntent', {
        detail: matchingIntent,
      })
    );
    expect(onIntentMock).toHaveBeenCalledWith(matchingIntent);
  });
});
