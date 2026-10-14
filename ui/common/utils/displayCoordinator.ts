/**
 * @file displayCoordinator.ts
 * @description Universal cross-display and cross-window navigation infrastructure.
 * Enables any element on any display to trigger navigation to any other element
 * on another display, delegating to an existing open window when available or
 * switching the local workspace display as a fallback.
 */

import { useEffect } from 'react';
import { emitToast } from './emitToast';

/**
 * Universal contract for navigating between displays and elements.
 */
export interface NavigationIntent<T = any> {
  /**
   * The destination workspace/display mode name
   * (e.g., 'Image Processing', 'Astronomy Manager', 'Planetarium', 'Observatory Manager', 'Observation Manager').
   */
  targetDisplay: string;

  /**
   * Optional identifier of the destination sub-element, panel, or tab
   * (e.g., 'fitsViewer', 'photometryPlot', 'spectrumViewer', 'targetList').
   */
  targetElement?: string;

  /**
   * Action name to trigger on the destination (e.g., 'targetSelected', 'astronomySelectStar', 'locateCoordinates').
   */
  action: string;

  /**
   * Data payload passed to the destination element.
   */
  payload: T;

  /**
   * Focus and visual highlight instructions for the destination element.
   */
  focus?: {
    scrollToElement?: boolean;
    flashHighlight?: boolean;
  };

  /**
   * Optional toast notification displayed to confirm the navigation.
   */
  toast?: {
    message: string;
    type?: 'info' | 'success' | 'warning' | 'error';
    title?: string;
  };
}

/**
 * Dispatches a navigation intent from any source element to a destination element.
 *
 * First checks whether an open Electron window is already presenting the requested
 * target display. If so, routes the intent to that window and focuses it without
 * disrupting the source window. Otherwise, switches the current window's display.
 *
 * @param intent The navigation intent specifying destination display, element, and payload.
 * @returns Result object indicating whether the intent was handled by another window.
 */
export async function navigateToElement<T = any>(
  intent: NavigationIntent<T>
): Promise<{ handledRemotely: boolean }> {
  // 1. Check if Electron can route this to an already open window running targetDisplay
  if (window.astrometrics?.app?.routeDisplayAction) {
    try {
      const response = await window.astrometrics.app.routeDisplayAction(intent);
      if (response?.handledRemotely) {
        if (intent.toast) {
          emitToast(
            intent.toast.message,
            intent.toast.type || 'info',
            intent.toast.title || intent.targetDisplay
          );
        }
        return { handledRemotely: true };
      }
    } catch (err) {
      console.warn('[displayCoordinator] routeDisplayAction failed, falling back to local navigation:', err);
    }
  }

  // 2. Local Fallback: switch local window display if not already active
  try {
    const isAuxWindow = Boolean(typeof window !== 'undefined' && window.location?.search && new URLSearchParams(window.location.search).get('windowId'));
    if (!isAuxWindow && typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('appMode', intent.targetDisplay);
    }
  } catch {
    // Ignore localStorage failures
  }

  window.dispatchEvent(
    new CustomEvent('astrometrics:modeChange', { detail: intent.targetDisplay })
  );

  // 3. Dispatch specific action and general navigationIntent locally
  window.dispatchEvent(
    new CustomEvent(`astrometrics:${intent.action}`, { detail: intent.payload })
  );
  window.dispatchEvent(
    new CustomEvent('astrometrics:navigationIntent', { detail: intent })
  );

  if (intent.toast) {
    emitToast(
      intent.toast.message,
      intent.toast.type || 'info',
      intent.toast.title || intent.targetDisplay
    );
  }

  return { handledRemotely: false };
}

/**
 * React hook for components/panels that act as navigation targets.
 * Listens for incoming navigation intents matching the given display and optional elementId.
 *
 * @param display The workspace display name this component belongs to.
 * @param elementId Optional specific element identifier within the display.
 * @param onIntent Callback invoked when a matching intent is received.
 */
export function useNavigationTarget(
  display: string,
  elementId?: string,
  onIntent?: (intent: NavigationIntent) => void
): void {
  useEffect(() => {
    const handleIntent = (event: Event) => {
      const intent = (event as CustomEvent<NavigationIntent>).detail;
      if (!intent) return;
      if (intent.targetDisplay !== display) return;
      if (elementId && intent.targetElement && intent.targetElement !== elementId) return;

      onIntent?.(intent);
    };

    window.addEventListener('astrometrics:navigationIntent', handleIntent);
    return () => window.removeEventListener('astrometrics:navigationIntent', handleIntent);
  }, [display, elementId, onIntent]);
}
