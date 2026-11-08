/**
 * @file displayCoordinator.ts
 * @description Universal cross-display and cross-window navigation infrastructure.
 * Enables any element on any display to trigger navigation to any other element
 * on another display, delegating to an existing open window when available or
 * switching the local workspace display as a fallback. The request waits in
 * the hand-off store (`displayHandoffStore.ts`) until the target display runs.
 */

import { emitToast } from './emitToast';
import { postHandoff } from './displayHandoffStore';

/**
 * Universal contract for navigating between displays and elements.
 */
export interface NavigationIntent<T = any> {
  /**
   * The destination workspace/display mode name
   * (e.g., 'Image Processing', 'Astronomy Manager', 'Planetarium', 'Observatory Manager', 'Observation Planner').
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
   * Optional toast, shown only when another window handled the request (in this window the display change itself is the feedback).
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

  // 3. Switch the display, then leave the request in the hand-off store. The
  // store keeps it until the target display is running, so it is not lost when
  // that display is hidden or paused at this moment.
  window.dispatchEvent(
    new CustomEvent('astrometrics:modeChange', { detail: intent.targetDisplay })
  );
  postHandoff(intent.targetDisplay, intent.action, intent.payload, { element: intent.targetElement });

  // No toast here: the display changing is the feedback. A toast is only for the
  // remote case above, where the request went to another window.
  return { handledRemotely: false };
}

/**
 * What the `showFrames` action carries to the Image Viewer: the quality rows a
 * statement cites as evidence, and the words of that statement.
 */
export interface ShowFramesPayload {
  /** Row ids in the quality table, in the order the statement lists them. */
  rowIds: number[];
  /** The target the statement is about. */
  targetId: string;
  /** The statement's own text, shown in the Image Viewer banner. */
  statementText: string;
}

/**
 * Asks the Image Viewer to list the quality rows a statement cites.
 *
 * Reuses an open Image Viewer window when there is one; otherwise this window
 * switches to the Image Viewer.
 *
 * @param payload The cited row ids, target and statement text.
 * @returns Result object indicating whether another window handled it.
 */
export function showFrames(payload: ShowFramesPayload): Promise<{ handledRemotely: boolean }> {
  return navigateToElement<ShowFramesPayload>({
    targetDisplay: 'Image Viewer',
    targetElement: 'citedFrames',
    action: 'showFrames',
    payload,
  });
}
