/**
 * @file imageViewStore.ts
 * @description Keeps the view of the Image Processing screen so the person can leave it (for the Astronomy
 * Manager, for example) and come back to the same place: the target, the file, the display mode, the
 * overlays that are on, and the zoom and the point of the picture at the center of the view. It is a
 * per-viewer convenience kept in local storage, so every read and write is guarded and the screen works without it.
 * The Astronomy Manager reads one flag from it, to show a "Back to image" button when the person came from a star in the picture.
 */

/** The key under which the record is kept. */
export const IMAGE_VIEW_STORAGE_KEY = 'astrometrics:imageProcessing:savedView';

/** The event sent when the record changes, so a screen that shows a button for it can update. */
export const IMAGE_VIEW_CHANGED_EVENT = 'astrometrics:imageViewSaved';

/** The action that asks Image Processing to show the saved view again. */
export const RETURN_TO_IMAGE_ACTION = 'returnToImage';

/** Where the view of the picture was: the point at the center and the zoom. */
export interface SavedViewport {
  /** The point at the center, as a fraction of the picture's width from its left edge. */
  x: number;
  /** The point at the center, as a fraction of the picture's height from its top edge. */
  y: number;
  /** The zoom, as a multiple of the zoom that fits the whole picture in the window. */
  zoomOverFit: number;
  /** How far the picture was shown turned, in quarter turns, when it was saved. */
  turn: number;
}

/** What is saved about the Image Processing screen. */
export interface SavedImageView {
  /** The selected target, or null. */
  target: string | null;
  /** The file on screen, or null. */
  file: string | null;
  /** The display mode, for example "linear" or "auto-stretch". */
  displayMode: string;
  /** True when the Astrometry overlay was on. */
  astrometry: boolean;
  /** True when the Detections overlay was on. */
  detections: boolean;
  /** Where the view was, or null when the picture had not been drawn. */
  view: SavedViewport | null;
  /** True when the person left the screen, so the next time it is shown it puts this view back. */
  returnPending: boolean;
  /** The star the person opened in the Astronomy Manager from the picture, or null. */
  arrivedFromStar: string | null;
  /** The library record of that star (the id the Astronomy Manager shows it under), or null when not known. */
  arrivedFromRecord?: string | null;
}

/**
 * The extra fields of a `targetSelected` hand-off to Image Processing that ask it to show one star: the target's
 * main stack with the Astrometry overlay on, the star selected, and the view centered and zoomed on it.
 */
export interface StarFocusHandoff {
  /** The target to open. */
  targetId: string;
  /** Turns the Astrometry overlay on. */
  enableAstrometry: true;
  /** The star's library record id, matched against the stack's named stars by their catalog keys. */
  focusLibraryStarId: string;
  /** The star's name, for the note shown when the stack does not name it. */
  focusStarName?: string;
}

/**
 * Checks that a value read from storage has the shape of a saved view.
 *
 * @param value What was parsed.
 * @returns True when every field has its type.
 */
function isSavedImageView(value: unknown): value is SavedImageView {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  const viewport = record.view as Record<string, unknown> | null | undefined;
  const viewportOk = viewport === null || viewport === undefined || (
    typeof viewport === 'object'
    && ['x', 'y', 'zoomOverFit', 'turn'].every((key) => typeof viewport[key] === 'number' && Number.isFinite(viewport[key] as number))
  );
  return (record.target === null || typeof record.target === 'string')
    && (record.file === null || typeof record.file === 'string')
    && typeof record.displayMode === 'string'
    && typeof record.astrometry === 'boolean'
    && typeof record.detections === 'boolean'
    && viewportOk
    && typeof record.returnPending === 'boolean'
    && (record.arrivedFromStar === null || typeof record.arrivedFromStar === 'string')
    && (record.arrivedFromRecord === undefined || record.arrivedFromRecord === null || typeof record.arrivedFromRecord === 'string');
}

/**
 * Reads the saved view.
 *
 * @returns The record, or null when there is none or storage cannot be read.
 */
export function loadImageView(): SavedImageView | null {
  try {
    const text = window.localStorage.getItem(IMAGE_VIEW_STORAGE_KEY);
    if (!text) return null;
    const parsed: unknown = JSON.parse(text);
    if (!isSavedImageView(parsed)) return null;
    return { ...parsed, view: parsed.view ?? null };
  } catch {
    return null;
  }
}

/**
 * Writes the saved view and tells listeners.
 *
 * @param record The whole record.
 */
export function saveImageView(record: SavedImageView): void {
  try {
    window.localStorage.setItem(IMAGE_VIEW_STORAGE_KEY, JSON.stringify(record));
    window.dispatchEvent(new CustomEvent(IMAGE_VIEW_CHANGED_EVENT));
  } catch {
    // The view is a convenience; without storage the screen keeps its own state.
  }
}

/**
 * Marks the saved view as put back, so it is not shown again the next time the screen is shown.
 */
export function markImageViewReturned(): void {
  const record = loadImageView();
  if (!record || (!record.returnPending && record.arrivedFromStar === null)) return;
  saveImageView({ ...record, returnPending: false, arrivedFromStar: null, arrivedFromRecord: null });
}

/**
 * Forgets the saved view, for example when the person opens another target on purpose.
 */
export function clearImageView(): void {
  try {
    window.localStorage.removeItem(IMAGE_VIEW_STORAGE_KEY);
    window.dispatchEvent(new CustomEvent(IMAGE_VIEW_CHANGED_EVENT));
  } catch {
    // Nothing to forget.
  }
}
