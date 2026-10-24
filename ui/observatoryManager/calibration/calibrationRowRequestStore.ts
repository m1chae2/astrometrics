/**
 * @file calibrationRowRequestStore.ts
 * @description Holds the latest `showCalibrationRow` request for the
 * Observatory Manager. The listener is registered when this file is first
 * imported, so a request that arrives before the display has mounted is kept
 * and handed over when the calibration library appears.
 */

import type { ShowCalibrationRowPayload } from './calibrationRowMatch';

type Listener = (request: ShowCalibrationRowPayload | null) => void;

const listeners = new Set<Listener>();
let currentRequest: ShowCalibrationRowPayload | null = null;

/**
 * Stores a request and tells subscribers.
 * @param event The `astrometrics:showCalibrationRow` event; its detail is the payload.
 */
function handleShowCalibrationRow(event: Event): void {
    const payload = (event as CustomEvent<ShowCalibrationRowPayload>).detail;
    if (!payload) return;
    currentRequest = payload;
    listeners.forEach((listener) => listener(currentRequest));
}

if (typeof window !== 'undefined') {
    window.addEventListener('astrometrics:showCalibrationRow', handleShowCalibrationRow);
}

/**
 * Returns the current request, or null when none is waiting.
 * @return The latest request.
 */
export function getCalibrationRowRequest(): ShowCalibrationRowPayload | null {
    return currentRequest;
}

/** Clears the request once the panel has shown it. */
export function clearCalibrationRowRequest(): void {
    currentRequest = null;
}

/**
 * Subscribes to new requests.
 * @param listener Called with each new request.
 * @return A function that removes the subscription.
 */
export function subscribeToCalibrationRows(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}
