/**
 * @file showOnSkyStore.ts
 * @description Holds the latest `showOnSky` request for the Planetarium.
 * The listener is registered when this file is first imported, so a request
 * that arrives before the Planetarium has been shown (the main window mounts
 * a display on its first visit) is kept and handed over when it mounts.
 */

/** What the `showOnSky` action carries: the target to center on. */
export interface ShowOnSkyPayload {
  /** The id of the target the user wants to see on the sky map. */
  targetId: string;
}

type Listener = (request: ShowOnSkyPayload | null) => void;

const listeners = new Set<Listener>();
let currentRequest: ShowOnSkyPayload | null = null;

/**
 * Tells every subscriber about the current request.
 */
function notify(): void {
  listeners.forEach((listener) => listener(currentRequest));
}

/**
 * Stores a `showOnSky` request and tells subscribers.
 *
 * @param event The `astrometrics:showOnSky` event; its detail is the payload.
 */
function handleShowOnSky(event: Event): void {
  const payload = (event as CustomEvent<ShowOnSkyPayload>).detail;
  if (!payload || typeof payload.targetId !== 'string' || payload.targetId === '') {
    return;
  }
  currentRequest = { targetId: payload.targetId };
  notify();
}

if (typeof window !== 'undefined') {
  window.addEventListener('astrometrics:showOnSky', handleShowOnSky);
}

/**
 * Returns the waiting request, or null when there is none.
 *
 * @returns The latest request that has not been handled.
 */
export function getShowOnSkyRequest(): ShowOnSkyPayload | null {
  return currentRequest;
}

/**
 * Clears the request once the Planetarium has acted on it.
 */
export function clearShowOnSkyRequest(): void {
  currentRequest = null;
  notify();
}

/**
 * Subscribes to request changes.
 *
 * @param listener Called with the new request, or null when cleared.
 * @returns A function that removes the subscription.
 */
export function subscribeToShowOnSky(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
