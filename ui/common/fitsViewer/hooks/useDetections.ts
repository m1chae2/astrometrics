/**
 * @file useDetections.ts
 * @description Fetches the stars the star finder detects on the image shown, for the Detections overlay.
 * It asks `images:measure_detections` once for each image while the overlay is on (the backend keeps the
 * answer, so turning the overlay off and on is quick) and ignores an answer that arrives after the image
 * changed. A refusal (an image outside the library) hides the overlay quietly; any other failure
 * gives a short message.
 */
import { useEffect, useState } from 'react';
import { callBackend } from '../../services/backendApi';
import { BackendError } from '../../services/backendError';
import type { DetectionsMeasurement } from '../../types/backendTypes';

/** What the overlay has to show. */
export type DetectionsState =
  | { status: 'off' }
  | { status: 'loading' }
  | { status: 'refused' }
  | { status: 'error'; message: string }
  | { status: 'ready'; measurement: DetectionsMeasurement };

/**
 * Tells whether a failure is the backend refusing the image, as it does for a file outside the library.
 *
 * @param failure Whatever was thrown.
 * @returns True for a refusal.
 */
export function isRefusal(failure: unknown): boolean {
  return failure instanceof BackendError && failure.code === 'invalid_argument';
}

/**
 * Loads the detections of one image while the overlay is on.
 *
 * @param enabled True while the overlay is on.
 * @param imageKey Changes when another image is shown, so the detections are fetched again.
 * @param resolvePath Finds the file path of the image on screen.
 * @returns What the overlay shows.
 */
export function useDetections(
  enabled: boolean,
  imageKey: unknown,
  resolvePath: (() => Promise<string | null>) | undefined,
): DetectionsState {
  const [state, setState] = useState<DetectionsState>({ status: 'off' });

  useEffect(() => {
    if (!enabled || !resolvePath) {
      setState({ status: 'off' });
      return;
    }
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      try {
        const path = await resolvePath();
        if (cancelled) return;
        if (!path) {
          setState({ status: 'refused' });
          return;
        }
        const measurement = await callBackend('images:measure_detections', { path }, { silent: true });
        if (!cancelled) setState({ status: 'ready', measurement });
      } catch (failure) {
        if (cancelled) return;
        setState(isRefusal(failure) ? { status: 'refused' } : { status: 'error', message: 'The stars could not be found on this image.' });
      }
    })();
    return () => { cancelled = true; };
    // The path resolver is read when the image or the switch changes, not each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, imageKey]);

  return state;
}
