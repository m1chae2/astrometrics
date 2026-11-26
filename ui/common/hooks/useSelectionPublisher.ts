/**
 * @file useSelectionPublisher.ts
 * @description Tells the backend what is picked in Image Processing: the target, the file on screen and the
 * selected star. The Command Console, which may be in another window, reads it as its `selection` object.
 * The hook waits until the pick has stayed the same for a short time, and it sends nothing when the pick is
 * the same as the last one sent. Sending is a quiet background task: if it fails (for example, an older
 * backend that does not know the call), nothing is shown and nothing is retried.
 */
import { useEffect, useRef } from 'react';
import { callBackend } from '../services/backendApi';
import type { AstrometryOverlayStar } from '../services/astronomyService';

/** How long, in milliseconds, the pick must stay the same before it is sent. */
export const SELECTION_PUBLISH_DELAY_MS = 300;

/** The selected star, in the form the backend's `ui:set_selection` call takes. */
export interface PublishedStar {
  /** The star's id on the picture (the key Image Processing selects it by). */
  id: string | null;
  /** The star's name, such as "Vega". */
  name: string | null;
  /** The id of the star's record in the library, when it has one. */
  library_id: string | null;
  /** Right ascension, in degrees. */
  ra: number | null;
  /** Declination, in degrees. */
  dec: number | null;
  /** Column of the star's center on the shown picture, in pixels counted from 0. */
  x: number | null;
  /** Row of the star's center on the shown picture, in pixels counted from 0. */
  y: number | null;
}

/** What is picked in Image Processing, in the form the backend's `ui:set_selection` call takes. */
export interface PublishedSelection {
  /** The target's name, or null. */
  target: string | null;
  /** The file on screen (a stack or a raw frame), or null. */
  path: string | null;
  /** "stack" or "frame", or null when no file is shown. */
  kind: 'stack' | 'frame' | null;
  /** The selected star, or null when no star is selected. */
  star: PublishedStar | null;
}

/**
 * Gives a number only when it is a real, finite number.
 *
 * @param value The value to check.
 * @returns The number, or null.
 */
function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Gives a text only when it is a non-empty string.
 *
 * @param value The value to check.
 * @returns The text, or null.
 */
function textOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

/**
 * Turns a star of the Astrometry overlay into the star the backend takes.
 *
 * The pixel position is copied as the overlay gives it: the column and row of the star's center on the shown
 * picture, counted from 0 (the first pixel is 0, as in NumPy and Astropy).
 *
 * @param star The overlay star, or null when no star is selected.
 * @returns The star to send, or null.
 */
export function publishedStarFromOverlay(star: AstrometryOverlayStar | null | undefined): PublishedStar | null {
  if (!star) return null;
  const id = textOrNull(star.id) ?? textOrNull(star.name);
  const name = textOrNull(star.name);
  const libraryId = textOrNull(star.libraryId);
  if (!id && !name && !libraryId) return null;
  return {
    id,
    name,
    library_id: libraryId,
    ra: finiteOrNull(star.raDeg),
    dec: finiteOrNull(star.decDeg),
    x: finiteOrNull(star.x),
    y: finiteOrNull(star.y),
  };
}

/**
 * Tells whether a selection holds nothing at all.
 *
 * @param selection The selection to check.
 * @returns True when the target, the file and the star are all empty.
 */
function isEmptySelection(selection: PublishedSelection): boolean {
  return selection.target === null && selection.path === null && selection.star === null;
}

/**
 * Sends the Image Processing pick to the backend each time it changes.
 *
 * The pick is sent once it has stayed the same for {@link SELECTION_PUBLISH_DELAY_MS}. A pick equal to the
 * last one sent is not sent again. The very first pick is not sent while it is empty, because the backend
 * starts with nothing selected. A failed send shows nothing and is not retried; the next change sends again.
 *
 * @param selection What is picked now.
 * @param enabled False to send nothing (for example, in a screen that is not Image Processing).
 */
export function useSelectionPublisher(selection: PublishedSelection, enabled = true): void {
  const lastSentRef = useRef<string | null>(null);
  const key = JSON.stringify(selection);

  useEffect(() => {
    if (!enabled) return undefined;
    if (key === lastSentRef.current) return undefined;
    const payload = JSON.parse(key) as PublishedSelection;
    if (lastSentRef.current === null && isEmptySelection(payload)) return undefined;
    const timer = window.setTimeout(() => {
      lastSentRef.current = key;
      // A background send: an older backend, or one that is restarting, is not an error to show.
      try {
        Promise.resolve(callBackend('ui:set_selection', payload, { silent: true, maxRetries: 0 })).catch(() => undefined);
      } catch {
        // The same: nothing to show.
      }
    }, SELECTION_PUBLISH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [key, enabled]);
}
