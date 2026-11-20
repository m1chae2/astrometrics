/**
 * @file useStarShapes.ts
 * @description Fetches how stretched the stars of the image shown are, for the Shapes overlay. It asks
 * `images:measure_star_shapes` once for each image while the overlay is on (the backend keeps the answer,
 * so turning the overlay off and on is quick) and ignores an answer that arrives after the image changed.
 * A refusal (an image outside the library) hides the overlay quietly; any other failure gives a short
 * message. An image with too few stars is an answer, not a failure.
 */
import { useEffect, useState } from 'react';
import { callBackend } from '../../services/backendApi';
import type { StarShapesMeasurement } from '../../types/backendTypes';
import { isRefusal } from './useDetections';

/** What the overlay has to show. */
export type StarShapesState =
  | { status: 'off' }
  | { status: 'loading' }
  | { status: 'refused' }
  | { status: 'error'; message: string }
  | { status: 'ready'; measurement: StarShapesMeasurement };

/**
 * Loads the star shapes of one image while the overlay is on.
 *
 * @param enabled True while the overlay is on.
 * @param imageKey Changes when another image is shown, so the shapes are fetched again.
 * @param resolvePath Finds the file path of the image on screen.
 * @returns What the overlay shows.
 */
export function useStarShapes(
  enabled: boolean,
  imageKey: unknown,
  resolvePath: (() => Promise<string | null>) | undefined,
): StarShapesState {
  const [state, setState] = useState<StarShapesState>({ status: 'off' });

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
        const measurement = await callBackend('images:measure_star_shapes', { path }, { silent: true });
        if (!cancelled) setState({ status: 'ready', measurement });
      } catch (failure) {
        if (cancelled) return;
        setState(isRefusal(failure) ? { status: 'refused' } : { status: 'error', message: 'The star shapes could not be measured on this image.' });
      }
    })();
    return () => { cancelled = true; };
    // The path resolver is read when the image or the switch changes, not each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, imageKey]);

  return state;
}
