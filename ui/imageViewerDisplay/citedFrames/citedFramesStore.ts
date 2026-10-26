/**
 * @file citedFramesStore.ts
 * @description Holds the latest `showFrames` request for the Image Viewer.
 * The listener is registered when this file is first imported, so a request
 * that arrives before the Image Viewer has been shown (the main window mounts
 * a display on its first visit) is kept and handed over when it mounts.
 * The store also remembers which mode the window showed before, for the
 * "Back to <mode>" button.
 */

import type { ShowFramesPayload } from '../../common/utils/displayCoordinator';

/** The mode name of the Image Viewer. */
export const IMAGE_VIEWER_MODE = 'Image Viewer';

/** A `showFrames` request together with the mode to go back to. */
export interface CitedFramesRequest extends ShowFramesPayload {
  /** The mode the window showed before the Image Viewer, or null when unknown. */
  previousMode: string | null;
}

type Listener = (request: CitedFramesRequest | null) => void;

const listeners = new Set<Listener>();
let currentRequest: CitedFramesRequest | null = null;
let currentMode: string | null = null;
let previousMode: string | null = null;

try {
  currentMode = window.localStorage.getItem('appMode');
} catch {
  currentMode = null;
}

/**
 * Tells every subscriber about the current request.
 */
function notify(): void {
  listeners.forEach((listener) => listener(currentRequest));
}

/**
 * Remembers the last two modes this window showed.
 *
 * @param event The `astrometrics:modeChange` event; its detail is the mode name.
 */
function handleModeChange(event: Event): void {
  const mode = String((event as CustomEvent).detail ?? '');
  if (mode && mode !== currentMode) {
    previousMode = currentMode;
    currentMode = mode;
  }
}

/**
 * Stores a `showFrames` request and tells subscribers.
 *
 * @param event The `astrometrics:showFrames` event; its detail is the payload.
 */
function handleShowFrames(event: Event): void {
  const payload = (event as CustomEvent<ShowFramesPayload>).detail;
  if (!payload || !Array.isArray(payload.rowIds)) {
    return;
  }
  const cameFromOtherMode = currentMode === IMAGE_VIEWER_MODE && previousMode !== IMAGE_VIEWER_MODE;
  currentRequest = {
    rowIds: payload.rowIds,
    targetId: payload.targetId,
    statementText: payload.statementText,
    previousMode: cameFromOtherMode ? previousMode : null,
  };
  notify();
}

if (typeof window !== 'undefined') {
  window.addEventListener('astrometrics:modeChange', handleModeChange);
  window.addEventListener('astrometrics:showFrames', handleShowFrames);
}

/**
 * Returns the current request, or null when none is showing.
 *
 * @returns The latest request.
 */
export function getCitedFramesRequest(): CitedFramesRequest | null {
  return currentRequest;
}

/**
 * Clears the request, which hides the banner and list.
 */
export function clearCitedFramesRequest(): void {
  currentRequest = null;
  notify();
}

/**
 * Subscribes to request changes.
 *
 * @param listener Called with the new request, or null when cleared.
 * @returns A function that removes the subscription.
 */
export function subscribeToCitedFrames(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Goes back to the mode shown before the Image Viewer and clears the request.
 * Does nothing to the mode when it is unknown.
 */
export function goBackToPreviousMode(): void {
  const target = currentRequest?.previousMode;
  clearCitedFramesRequest();
  if (target) {
    window.dispatchEvent(new CustomEvent('astrometrics:modeChange', { detail: target }));
  }
}
