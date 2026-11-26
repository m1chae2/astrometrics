/**
 * @file useStarFocusRequest.ts
 * @description Shows one star that another screen asked for (the Astronomy Manager's "Back to image" button):
 * waits for the target and its files, shows the target's main stack, waits for the stack's star names, then
 * selects the star and asks the viewer to center and zoom on it. When the stack does not name the star, the
 * stack stays on screen and a short note says so.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AstrometryOverlayStar } from '../../common/services/astronomyService';
import type { RestoreView } from '../../common/fitsViewer/FitsRenderer';

/**
 * Screen pixels per stack pixel when the view zooms in on a star: the star fills a few screen pixels per
 * pixel and a few arcminutes of sky around it stay in view on a typical window. It is the same zoom
 * Blink starts from (`BLINK_START_ZOOM` is 4); 3 leaves a little more sky around the star.
 */
export const STAR_FOCUS_SCALE = 3;

/** One star to show. */
export interface StarFocusRequest {
  /** The target the star belongs to. */
  targetId: string;
  /** The star's library record id. */
  recordId: string;
  /** The star's name, for the note. */
  starName: string;
}

/** What the hook needs from the screen. */
export interface StarFocusInputs {
  /** The target selected now. */
  selectedTarget: string;
  /** True while the target's files are read. */
  isLoadingFiles: boolean;
  /** The target's main stack, or null when it has none. */
  mainStackPath: string | null;
  /** True when a stack is on screen. */
  isStackShown: boolean;
  /** True once the screen has chosen a file for the target, so the main stack is not replaced by that choice. */
  hasChosenFile: boolean;
  /** Puts the main stack on screen (and the stack picker and filters back to it). */
  showMainStack: () => void;
  /** The stack's star names and the job that makes them. */
  identification: {
    isLoaded: boolean;
    running: boolean;
    hasResult: boolean;
    stars: AstrometryOverlayStar[];
    start: () => Promise<void> | void;
  };
  /** Selects the star and moves the view. */
  onFound: (star: AstrometryOverlayStar, view: RestoreView | null) => void;
}

/**
 * Finds the stack's star that has a given library record, by the record id the star carries.
 *
 * @param stars The stack's named stars.
 * @param recordId The library record id.
 * @returns The star, or null.
 */
export function findStarForRecord(stars: readonly AstrometryOverlayStar[], recordId: string): AstrometryOverlayStar | null {
  return stars.find((star) => star.libraryId === recordId) ?? null;
}

/**
 * Builds the view that centers a star and zooms in on it.
 *
 * @param star The star, placed on the picture as drawn.
 * @param key Names the request, so the viewer applies it once.
 * @returns The view, or null when the star's picture size is not known.
 */
export function starFocusView(star: AstrometryOverlayStar, key: string): RestoreView | null {
  const width = star.referenceWidth ?? 0;
  const height = star.referenceHeight ?? 0;
  if (width <= 0 || height <= 0) return null;
  return {
    key,
    view: { x: star.x / width, y: star.y / height, zoomOverFit: 1, turn: 0 },
    scale: STAR_FOCUS_SCALE,
    pictureWidth: width,
    // The star profile panel opens for the star, the same as a click on it.
    pickStar: true,
  };
}

/**
 * Runs a request to show one star on its target's main stack.
 *
 * @param inputs See `StarFocusInputs`.
 * @returns `request` starts a new request; `note` is the short note to show, or null.
 */
export function useStarFocusRequest(inputs: StarFocusInputs): {
  request: (request: StarFocusRequest) => void;
  note: string | null;
} {
  // `stage` is where the request is: show the stack, then read its names, then (if needed) make them.
  const [pending, setPending] = useState<(StarFocusRequest & { stage: 'stack' | 'names' | 'naming' }) | null>(null);
  const [note, setNote] = useState<{ text: string; target: string } | null>(null);
  const countRef = useRef(0);
  // True once the naming job this request started has been seen running, so its end can be told apart.
  const sawNamingRef = useRef(false);
  const latestRef = useRef(inputs);
  useLayoutEffect(() => {
    latestRef.current = inputs;
  });

  const request = useCallback((next: StarFocusRequest) => {
    setNote(null);
    sawNamingRef.current = false;
    setPending({ ...next, stage: 'stack' });
  }, []);

  const { selectedTarget, isLoadingFiles, mainStackPath, isStackShown, hasChosenFile, identification } = inputs;
  const { isLoaded, running, hasResult, stars } = identification;

  useEffect(() => {
    if (!pending || selectedTarget !== pending.targetId || isLoadingFiles) return;
    const finish = (text: string | null): void => {
      setPending(null);
      setNote(text ? { text, target: pending.targetId } : null);
    };
    if (pending.stage === 'stack') {
      if (!mainStackPath) {
        finish(`${pending.targetId} has no stack, so ${pending.starName || 'the star'} cannot be shown on it.`);
        return;
      }
      if (!hasChosenFile) return;
      latestRef.current.showMainStack();
      setPending({ ...pending, stage: 'names' });
      return;
    }
    if (running) sawNamingRef.current = true;
    if (!isStackShown || !isLoaded || running) return;
    if (!hasResult) {
      if (pending.stage === 'names') {
        void latestRef.current.identification.start();
        setPending({ ...pending, stage: 'naming' });
        return;
      }
      if (!sawNamingRef.current) return;
      finish(`This stack has no star names, so ${pending.starName || 'the star'} could not be found on it.`);
      return;
    }
    const star = findStarForRecord(stars, pending.recordId);
    if (!star) {
      finish(`${pending.starName || 'This star'} is not among the named stars of this stack.`);
      return;
    }
    countRef.current += 1;
    latestRef.current.onFound(star, starFocusView(star, `star-focus-${countRef.current}`));
    finish(null);
  }, [pending, selectedTarget, isLoadingFiles, mainStackPath, isStackShown, hasChosenFile, isLoaded, running, hasResult, stars]);

  // The note belongs to the target it was written for.
  useEffect(() => {
    if (note && note.target !== selectedTarget) setNote(null);
  }, [note, selectedTarget]);

  return { request, note: note?.text ?? null };
}
