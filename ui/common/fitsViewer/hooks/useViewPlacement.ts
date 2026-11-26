/**
 * @file useViewPlacement.ts
 * @description Decides where the FITS viewer's view sits when a picture is drawn. It keeps the
 * zoom and the point at the center when a preview is replaced by the full image or by another
 * picture of the same target, fits a new target afresh, puts a focus star at the center, puts a
 * saved view back, and tells the caller where the view is.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ParsedFitsData } from './useFitsLoader';
import { fitScaleFor, type useCanvasInteraction } from './useCanvasInteraction';
import { askWorkerForPixel } from '../utils/pixelQuery';
import { baseToTurned, fitsPixelToTurnedPicture, rowOrderProbeCell, turnedSize } from '../displayTurn';
import type { QuarterTurn } from '../displayTurn';
import { fractionAtSky, skyAtFraction } from '../utils/transformUtils';
import type { Fraction, ImageGeometry } from '../utils/transformUtils';
import type { SkyPosition } from '../wcs';
import type { SavedViewport } from '../../utils/imageViewStore';

/** A star to put at the center of the view, for example the reference star of the Blink comparison. */
export interface FocusStar {
  /** The star's column in the file, counted from 1 (FITS pixels). */
  x: number;
  /** The star's row in the file, counted from 1 (FITS pixels). */
  y: number;
  /** The file's width in pixels. */
  width: number;
  /** The file's height in pixels. */
  height: number;
  /** Names the focus. The view is moved once for each picture drawn while the key stays the same. */
  key: string;
  /** Screen pixels per file pixel to zoom to when the view moves, or undefined to keep the zoom. */
  scale?: number;
}

/** A view to put back, with a key that changes for each request. */
export interface RestoreView {
  /** Names the request. The view is applied once for each key. */
  key: string;
  /** The point at the center and the zoom. */
  view: SavedViewport;
  /**
   * Screen pixels per picture pixel to zoom to, in place of `view.zoomOverFit`, for example to show one star
   * and the sky around it at the same size on any window.
   */
  scale?: number;
  /** The picture's width in pixels that `scale` refers to, used until the file's own size is known. */
  pictureWidth?: number;
  /**
   * Also picks the star at the new center, the same as a click there: the star profile panel opens for it.
   * It waits until the file's pixels are drawn, and is done once for each key.
   */
  pickStar?: boolean;
}

/** The longest, in milliseconds, the picture stays hidden while it waits for a view to be put back. */
export const RESTORE_HIDE_LIMIT_MS = 1500;

/** For this long, in milliseconds, after a view is put back, a new drawn size puts it back again. */
export const RESTORE_REAPPLY_WINDOW_MS = 800;

/** What `useViewPlacement` needs from the viewer. */
export interface ViewPlacementInput {
  /** The image asked for now (its Blob, or else its address). */
  currentKey: unknown;
  /** The selected target; another picture of the same target keeps the view. */
  selectedTarget: string | null;
  /** The full image when it is ready to draw for `currentKey`, else null. */
  mainObject: ParsedFitsData | ImageBitmap | null;
  /** The decoded preview picture, or null. */
  previewBitmap: ImageBitmap | null;
  /** True while the full image is not ready, so the preview may be drawn. */
  previewAllowed: boolean;
  /** The decoded FITS data, or null for a bitmap. */
  parsedData: ParsedFitsData | null;
  /** The decoding worker, which names the file pixel under a point. */
  workerRef: React.RefObject<Worker | null>;
  /** The quarter turn the picture is shown with. */
  turn: QuarterTurn;
  /** The size of the picture as shown (the file's size, swapped for a quarter turn). */
  shownSize: { w: number; h: number };
  /** The size the picture is drawn at on the canvas. */
  drawnSize: { w: number; h: number };
  /** The viewer's container. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** The pan and zoom state (`useCanvasInteraction`). */
  view: ReturnType<typeof useCanvasInteraction>;
  /** Set to true to keep the view through the next change of drawn size (shared with `useCanvasInteraction`). */
  keepViewOnResizeRef: React.RefObject<boolean>;
  /** A star to put at the center of the view each time a picture is drawn. */
  focusStar: FocusStar | null;
  /** A view to put back once for each key. */
  restoreView: RestoreView | null;
  /** Called after the view changes once a picture is drawn. */
  onViewChange?: (view: SavedViewport) => void;
  /** Called with true when the view is at the smallest zoom, and with false otherwise. */
  onMinimumZoomChange?: (atMinimum: boolean) => void;
  /** Fits the view again each time this number changes. */
  autoPanTrigger: number;
}

/** What `useViewPlacement` gives back. */
export interface ViewPlacement {
  /** Tells the hook what the canvas drawer just drew. */
  handleDrawn: (drawn: ParsedFitsData | ImageBitmap) => void;
  /** True once the full image is on the canvas. */
  mainDrawn: boolean;
  /** True while the preview is on the canvas. */
  previewDrawn: boolean;
  /** True once anything (the preview or the full image) is on the canvas. */
  firstFrameDrawn: boolean;
  /** True while the picture stays hidden until a saved view is put back. */
  restoreHidesPicture: boolean;
  /** The key of the last saved view put back, or null. */
  appliedRestoreKey: string | null;
}

/**
 * Places the view on each newly drawn picture and reports where it is.
 *
 * @param input The picture, the view and the requests (see `ViewPlacementInput`).
 * @returns What was drawn and whether a saved view still waits (see `ViewPlacement`).
 */
export function useViewPlacement(input: ViewPlacementInput): ViewPlacement {
  const {
    currentKey, selectedTarget, mainObject, previewBitmap, previewAllowed, parsedData, workerRef, turn,
    shownSize, drawnSize, containerRef, view, keepViewOnResizeRef, focusStar, restoreView,
    onViewChange, onMinimumZoomChange, autoPanTrigger,
  } = input;
  const { zoom, panX, panY, atMinimumZoom, resetView, getCenterFraction, placeCenter } = view;

  // What is on the canvas, as reported by the drawer after each draw.
  const [drawnObject, setDrawnObject] = useState<ParsedFitsData | ImageBitmap | null>(null);
  const mainDrawn = mainObject !== null && drawnObject === mainObject;
  // The preview is drawn only until the full image is ready.
  const previewObject = previewAllowed ? previewBitmap : null;
  const previewDrawn = previewObject !== null && drawnObject === previewObject;
  const firstFrameDrawn = mainDrawn || previewDrawn;

  // The newest values, for the callbacks and effects below that must not restart when they change.
  // They are updated before any passive effect (the canvas drawer's included) runs.
  const turnRef = useRef<QuarterTurn>(turn);
  const previewShownRef = useRef(previewDrawn);
  const previewBitmapRef = useRef<ImageBitmap | null>(previewBitmap);
  const targetRef = useRef(selectedTarget);
  const centerFractionRef = useRef(getCenterFraction);
  const onViewChangeRef = useRef(onViewChange);
  const onMinimumZoomChangeRef = useRef(onMinimumZoomChange);
  const resetViewRef = useRef(resetView);
  useLayoutEffect(() => {
    turnRef.current = turn;
    previewShownRef.current = previewDrawn;
    previewBitmapRef.current = previewBitmap;
    targetRef.current = selectedTarget;
    centerFractionRef.current = getCenterFraction;
    onViewChangeRef.current = onViewChange;
    onMinimumZoomChangeRef.current = onMinimumZoomChange;
    resetViewRef.current = resetView;
  });
  // What was drawn last, so a different picture of the same target can take over the view.
  const lastImageRef = useRef<{ object: ParsedFitsData | ImageBitmap; target: string | null; isMain: boolean; geometry: ImageGeometry } | null>(null);
  // The point of the picture at the center of the view when it was replaced, and the sky position there.
  const carryRef = useRef<{ fraction: Fraction; sky: SkyPosition | null } | null>(null);
  // True when the picture drawn next belongs to another target and starts from a fresh fit.
  const freshFitRef = useRef(false);

  const handleDrawn = useCallback((drawn: ParsedFitsData | ImageBitmap) => {
    const previous = lastImageRef.current;
    const isPreview = drawn === previewBitmapRef.current;
    const target = targetRef.current ?? null;
    if (previous && previous.object !== drawn) {
      if (previewShownRef.current && !isPreview) {
        // Replacing the preview with the full image: keep the zoom and position.
        keepViewOnResizeRef.current = true;
      } else if (target !== null && target === previous.target) {
        // Another picture of the same target: keep the zoom and the point at the center of the view.
        keepViewOnResizeRef.current = true;
        const fraction = previous.isMain ? centerFractionRef.current() : null;
        if (fraction) carryRef.current = { fraction, sky: skyAtFraction(fraction, previous.geometry) };
      } else {
        keepViewOnResizeRef.current = false;
        carryRef.current = null;
        freshFitRef.current = true;
      }
    }
    const geometry: ImageGeometry = 'imageId' in drawn
      ? { width: drawn.w, height: drawn.h, header: drawn.header, turn: turnRef.current }
      : { width: drawn.width, height: drawn.height, turn: turnRef.current };
    lastImageRef.current = { object: drawn, target, isMain: !isPreview, geometry };
    setDrawnObject(drawn);
  }, [keepViewOnResizeRef]);

  // After a different picture is drawn: fit again for a new target, or put the sky position
  // (or else the relative position) that was at the center of the view back at the center.
  useEffect(() => {
    const image = lastImageRef.current;
    if (!drawnObject || !image || image.object !== drawnObject) return;
    if (freshFitRef.current) {
      freshFitRef.current = false;
      resetView();
    } else if (carryRef.current && image.isMain) {
      const { fraction, sky } = carryRef.current;
      const matched = sky ? fractionAtSky(sky, image.geometry) : null;
      placeCenter(matched ?? fraction);
      carryRef.current = null;
    }
    keepViewOnResizeRef.current = false;
    // Only a newly drawn picture matters here, not later changes to the view functions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawnObject]);

  // A different image starts from a fresh fit.
  useEffect(() => {
    keepViewOnResizeRef.current = false;
  }, [currentKey, keepViewOnResizeRef]);

  // Put the focus star at the center of the view, once for each picture drawn under the same focus key.
  const focusAppliedRef = useRef<{ key: string; object: unknown } | null>(null);
  useEffect(() => {
    if (!focusStar || !mainDrawn || !drawnObject || drawnSize.w <= 0 || drawnSize.h <= 0) return undefined;
    const applied = focusAppliedRef.current;
    if (applied && applied.key === focusStar.key && applied.object === drawnObject) return undefined;
    let cancelled = false;
    const worker = workerRef.current;
    if (parsedData && worker) {
      // The file's own row order decides which end of the picture the first row is at.
      const probeCell = rowOrderProbeCell(parsedData.w, parsedData.h, turn);
      void askWorkerForPixel(worker, parsedData.imageId, probeCell).then((reading) => {
        const firstRowAtTop = reading?.y === 1;
        const turned = fitsPixelToTurnedPicture(focusStar.x, focusStar.y, parsedData.w, parsedData.h, turn, firstRowAtTop);
        const shown = turnedSize(parsedData.w, parsedData.h, turn);
        if (cancelled) return;
        const scale = focusStar.scale === undefined ? undefined : (focusStar.scale * shown.w) / drawnSize.w;
        focusAppliedRef.current = { key: focusStar.key, object: drawnObject };
        placeCenter({ x: turned.x / shown.w, y: turned.y / shown.h }, { force: true, scale });
      });
    } else if (!parsedData) {
      // A picture the backend drew shows the file with its first row at the bottom, scaled to fit.
      // Turning a point given as fractions of a square gives the fractions of the turned picture.
      const place = baseToTurned(
        { x: (focusStar.x - 0.5) / focusStar.width, y: 1 - (focusStar.y - 0.5) / focusStar.height }, 1, 1, turn
      );
      const fileShown = turnedSize(focusStar.width, focusStar.height, turn);
      const scale = focusStar.scale === undefined ? undefined : (focusStar.scale * fileShown.w) / drawnSize.w;
      focusAppliedRef.current = { key: focusStar.key, object: drawnObject };
      placeCenter({ x: place.x, y: place.y }, { force: true, scale });
    }
    return () => { cancelled = true; };
  }, [focusStar, mainDrawn, drawnObject, drawnSize.w, drawnSize.h, parsedData, turn, placeCenter, workerRef]);

  // Put a saved view back (the person returned to this screen): the point at the center and the zoom.
  // A picture that is not drawn yet stays hidden until the view is in place, so the fitted view never shows first.
  const restoreKey = restoreView?.key ?? null;
  const [restoreRequest, setRestoreRequest] = useState<{ key: string; hide: boolean } | null>(null);
  if (restoreKey !== (restoreRequest?.key ?? null)) {
    setRestoreRequest(restoreKey ? { key: restoreKey, hide: !firstFrameDrawn } : null);
  }
  const [appliedRestoreKey, setAppliedRestoreKey] = useState<string | null>(null);
  const [timedOutRestoreKey, setTimedOutRestoreKey] = useState<string | null>(null);
  const restoreWaiting = restoreKey !== null && appliedRestoreKey !== restoreKey && timedOutRestoreKey !== restoreKey;
  const restoreHidesPicture = restoreWaiting && Boolean(restoreRequest?.hide);
  const lastRestoreRef = useRef<{ key: string; w: number; h: number; at: number } | null>(null);
  useEffect(() => {
    if (!restoreView || !firstFrameDrawn || drawnSize.w <= 0 || drawnSize.h <= 0) return;
    const container = containerRef.current;
    const fitScale = container ? fitScaleFor(container.clientWidth, container.clientHeight, drawnSize.w, drawnSize.h) : 0;
    if (fitScale <= 0) return;
    const last = lastRestoreRef.current;
    const sameRequest = last !== null && last.key === restoreView.key;
    // A change of the drawn size just after the view was put back (the preview replaced by the full image, or the first
    // fit) is put back again; a change long after it is the person's own doing and is left alone.
    if (sameRequest && last.w === drawnSize.w && last.h === drawnSize.h) return;
    if (sameRequest && Date.now() - last.at > RESTORE_REAPPLY_WINDOW_MS) return;
    lastRestoreRef.current = { key: restoreView.key, w: drawnSize.w, h: drawnSize.h, at: sameRequest ? last.at : Date.now() };
    const { x, y, zoomOverFit } = restoreView.view;
    // A zoom given in screen pixels per picture pixel is turned into screen pixels per drawn pixel.
    const pictureWidth = shownSize.w || restoreView.pictureWidth || 0;
    const scale = restoreView.scale !== undefined && pictureWidth > 0
      ? (restoreView.scale * pictureWidth) / drawnSize.w
      : zoomOverFit * fitScale;
    placeCenter({ x, y }, { force: true, scale });
    setAppliedRestoreKey(restoreView.key);
  }, [restoreView, firstFrameDrawn, drawnSize.w, drawnSize.h, placeCenter, shownSize.w, containerRef]);
  // A view that cannot be put back (the picture never draws) must not leave the picture hidden.
  useEffect(() => {
    if (!restoreKey || !restoreHidesPicture) return undefined;
    const timer = window.setTimeout(() => setTimedOutRestoreKey(restoreKey), RESTORE_HIDE_LIMIT_MS);
    return () => window.clearTimeout(timer);
  }, [restoreKey, restoreHidesPicture]);

  // Tell the caller where the view is, so it can be saved and put back later. Nothing is sent while a saved view waits to be put back.
  useEffect(() => {
    if (!firstFrameDrawn || restoreWaiting) return;
    const container = containerRef.current;
    const fitScale = container ? fitScaleFor(container.clientWidth, container.clientHeight, drawnSize.w, drawnSize.h) : 0;
    const center = getCenterFraction();
    if (!center || fitScale <= 0 || zoom <= 0) return;
    onViewChangeRef.current?.({ x: center.x, y: center.y, zoomOverFit: zoom / fitScale, turn });
  }, [firstFrameDrawn, restoreWaiting, zoom, panX, panY, drawnSize.w, drawnSize.h, turn, getCenterFraction, containerRef]);

  // Tell the caller whether the view is at the smallest zoom, so it can disable its zoom-out control.
  useEffect(() => {
    onMinimumZoomChangeRef.current?.(atMinimumZoom);
  }, [atMinimumZoom]);

  // Fit again only when the trigger itself changes. The reset function changes whenever the drawn
  // size does (a new display stretch draws the picture at another size), and that must not refit.
  const lastAutoPanTriggerRef = useRef(autoPanTrigger);
  useEffect(() => {
    if (lastAutoPanTriggerRef.current === autoPanTrigger) return;
    lastAutoPanTriggerRef.current = autoPanTrigger;
    if (autoPanTrigger) resetViewRef.current();
  }, [autoPanTrigger]);

  return { handleDrawn, mainDrawn, previewDrawn, firstFrameDrawn, restoreHidesPicture, appliedRestoreKey };
}
