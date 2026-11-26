/**
 * @file FitsRenderer.tsx
 * @description Renders FITS format and standard bitmap images onto an interactive HTML5 canvas.
 * Manages rendering worker delegation, zoom/pan transforms, and auto-stretch display.
 */
import React, { useState, useRef, useEffect, useLayoutEffect, useCallback, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader, ParsedFitsData } from './hooks/useFitsLoader';
import { fitScaleFor, useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';
import { usePreviewBitmap } from './hooks/usePreviewBitmap';
import { useBitmapTurn } from './hooks/useBitmapTurn';
import { PixelReadout } from './PixelReadout';
import { StarProfilePanel, SET_DISPLAY_TO_LINEAR_MESSAGE } from './StarProfilePanel';
import type { StarProfileView } from './StarProfilePanel';
import type { SavedViewport } from '../utils/imageViewStore';
import { useStarProfile } from './hooks/useStarProfile';
import { useDetections } from './hooks/useDetections';
import { useStarIdentity } from './hooks/useStarIdentity';
import { buildDetectionPaths } from './utils/detectionMarkers';
import { askWorkerForPixel, pointerToFileCell } from './utils/pixelQuery';
import type { FileCell } from './utils/pixelQuery';
import { baseToTurned, describeTurn, fitsPixelToTurnedPicture, rowOrderProbeCell, turnedSize, turnStep, turnToMatchStack } from './displayTurn';
import type { QuarterTurn, StackOrientation } from './displayTurn';
import { Badge } from '../components/Badge';
import { fractionAtSky, skyAtFraction } from './utils/transformUtils';
import type { Fraction, ImageGeometry } from './utils/transformUtils';
import type { SkyPosition } from './wcs';
import { createPixelToSky } from './wcs';
import { canOpenInAstronomyManager, catalogKeysOfStar, findStarAt, nameStarAt } from './utils/starNames';
import { AstrometryOverlayStar } from '../services/astronomyService';
import { formatStarListLabel } from '../utils/starDisplayFormat';
import { Skeleton, UpdatingBar } from '../components/Skeleton';
import { StretchParameters } from '../types/backendTypes';
import {
  StarScreenTransform,
  chooseLabeledStarIds,
  MARKER_ALL_STARS_SCALE,
  findStarNearPointer,
  isInView,
  starScreenPosition
} from './utils/starOverlayLayout';

/**
 * Shortens long catalog identifiers for HUD reticle badges.
 *
 * Shortens coordinate names from common surveys (2MASS, GPM, SDSS) while
 * keeping enough digits that two stars in one field never share a label, and delegating to standard
 * catalog label formatters for Gaia and unnamed field detections.
 *
 * @param name Full star name or catalog identifier.
 * @returns Concise, human-readable display label.
 */
export function formatStarDisplayName(name: string): string {
  if (!name) return '';
  // Strip verbose cluster member prefix: e.g. "CI* NGC 6205 KAD 656" -> "KAD 656"
  const clusterMemberMatch = name.match(/^(?:CI\*|Cl\*)\s*(?:NGC\s*\d+|IC\s*\d+|M\s*\d+)\s+(.+)$/i);
  if (clusterMemberMatch) {
    name = clusterMemberMatch[1].trim();
  }
  // Survey names encode the star's position. Keep whole seconds of RA and
  // Dec (a few arcseconds), so two stars in one field never share a label:
  // 2MASS J16413289+3626285 -> 2MASS 164132+362628.
  const massMatch = name.match(/2MASS\s*J?([0-9]{6})[0-9]*([+-][0-9]{6})[0-9]*/i);
  if (massMatch) {
    return `2MASS ${massMatch[1]}${massMatch[2]}`;
  }
  // GPM 258.328551+36.535702 -> GPM 258.329+36.536 (degrees to about 4 arcseconds).
  const gpmMatch = name.match(/GPM\s*([0-9]+\.[0-9]+)([+-][0-9]+\.[0-9]+)/i);
  if (gpmMatch) {
    return `GPM ${Number(gpmMatch[1]).toFixed(3)}${gpmMatch[2][0]}${Math.abs(Number(gpmMatch[2])).toFixed(3)}`;
  }
  // SDSS J164132.89+362628.5 -> SDSS 164132+362628.
  const sdssMatch = name.match(/SDSS\s*J?([0-9]{6})[0-9.]*([+-][0-9]{6})[0-9.]*/i);
  if (sdssMatch) {
    return `SDSS ${sdssMatch[1]}${sdssMatch[2]}`;
  }
  return formatStarListLabel(name);
}

/**
 * Checks if a given overlay star matches the currently selected star identifier.
 *
 * The match is exact and ignores case: the star's own id, its catalog keys (Gaia DR3 id and name), or
 * its display name with cluster prefixes removed (so "Cl* NGC 6205 KAD 656" matches "KAD 656").
 * It never matches part of an id, so "detection-5" does not select "detection-50".
 *
 * @param star Astrometry overlay star item.
 * @param selectedId Target selected star identifier string.
 * @returns True if star matches the selection.
 */
export function isStarSelected(star: AstrometryOverlayStar, selectedId?: string | null): boolean {
  if (!selectedId) return false;
  const wanted = selectedId.trim().toLowerCase();
  if (!wanted) return false;
  const keys = [star.id || '', ...catalogKeysOfStar(star)].map((key) => key.trim().toLowerCase());
  if (keys.includes(wanted)) return true;

  // Compare formatted display names (e.g. "Cl* NGC 6205 KAD 656" vs "CI* NGC 6205 KAD 656" both normalize to "KAD 656")
  const normSelected = formatStarDisplayName(selectedId).trim().toLowerCase();
  const normStarName = formatStarDisplayName(star.name || star.id).trim().toLowerCase();
  return Boolean(normSelected && normStarName && normSelected === normStarName);
}

/**
 * Calculates badge horizontal offset (badgeX) relative to the star reticle.
 * Defaults to placing the badge on the right side of the reticle (+20px) unless it
 * would overflow the right edge of the container viewport, in which case it flips left.
 *
 * @param screenX Horizontal position of the star in screen/container pixels.
 * @param badgeWidth Rendered width of the star label badge.
 * @param containerW Width of the containing viewport element.
 * @returns Offset in pixels from star center to badge origin.
 */
export function computeBadgePlacement(screenX: number, badgeWidth: number, containerW: number): number {
  const placeLeft = (screenX + 20 + badgeWidth) > containerW;
  return placeLeft ? -badgeWidth - 20 : 20;
}

/**
 * Estimates the width of a star's label badge.
 *
 * @param star Astrometry overlay star item.
 * @returns Badge width in screen pixels.
 */
export function estimateBadgeWidth(star: AstrometryOverlayStar): number {
  const nameLength = formatStarDisplayName(star.name).length;
  return Math.max(56, nameLength * 7.6 + 26);
}

/** Radius, in screen pixels, of the thin circle drawn on every star. */
const MARKER_RADIUS_PX = 5;

/** Radius, in screen pixels, of the smaller marks for unmatched and missed stars when zoomed out. */
const SMALL_MARKER_RADIUS_PX = 3;

/** How far from a click, in screen pixels, a measurement may look for the star's center. */
export const STAR_SEARCH_SCREEN_PX = 6;

/** Smallest search radius sent to the backend, in image pixels. */
export const STAR_SEARCH_MIN_PX = 3;

/** Largest search radius sent to the backend, in image pixels (the backend caps it at 50 too). */
export const STAR_SEARCH_MAX_PX = 50;

/**
 * Turns the star search distance of a click (6 screen pixels) into image pixels at a zoom.
 *
 * @param screenPixelsPerFilePixel The zoom, in screen pixels per image pixel.
 * @returns The search radius, from 3 to 50 image pixels.
 */
export function searchRadiusAtScale(screenPixelsPerFilePixel: number): number {
  return Math.min(STAR_SEARCH_MAX_PX, Math.max(STAR_SEARCH_MIN_PX, STAR_SEARCH_SCREEN_PX / screenPixelsPerFilePixel));
}

/** Radius, in screen pixels, of the small marker drawn when the profile does not look like a star. */
const NOT_A_STAR_MARKER_RADIUS_PX = 4;

/** Most the pointer may move, in screen pixels, between press and release for the gesture to count as a click and not a pan. */
export const STAR_CLICK_MOVE_THRESHOLD_PX = 4;

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

export interface FitsRendererProps {
  imageUrl: string | null;
  imageBlob: Blob | null;
  frameInfo: any | null;
  loading: boolean;
  error: string | null;
  selectedTarget: string | null;
  autoPanTrigger: number;
  disableStretch?: boolean;
  stretch?: boolean;
  /** Pixel values drawn black and white when `stretch` is false (default: the file's own range). */
  displayRange?: readonly [number, number];
  /** The library's automatic stretch for a FITS image, sent by the backend; without it a stretched view is linear. */
  stretchParameters?: StretchParameters | null;
  overlayStars?: AstrometryOverlayStar[];
  showOverlay?: boolean;
  onStarClick?: (star: AstrometryOverlayStar) => void;
  selectedStarId?: string | null;
  /**
   * A small stretched picture (JPEG) of the same image. It is drawn at once while the
   * full FITS image is read and decoded, then replaced by it without moving the view.
   * Overlays that need FITS pixel positions wait for the full image.
   */
  previewBlob?: Blob | null;
  /** When true, this viewer keeps pictures decoded ahead of time (see `prefetchDecode`). */
  acceptPrefetch?: boolean;
  /** True while the Detections overlay is on: a marker at every star the astrometry run's star finder detects. */
  showDetections?: boolean;
  /** Finds the file path of the image on screen, so the backend can measure that file. Called for each measurement. */
  resolveMeasurePath?: () => Promise<string | null>;
  /** Shown in the star profile panel in place of a measurement when the image holds no pixel values (default: ask for Linear). */
  noPixelDataMessage?: string;
  /** A sentence about the picture shown in the star profile panel, for example that it is already stretched. */
  starProfilePictureNote?: string;
  /**
   * The target's stack orientation. A raw frame is then shown turned by the quarter turn that makes it
   * match the stack (display only), so frames from both sides of a meridian flip look the same way round.
   * Leave it out to show every frame as it is stored.
   */
  stackOrientation?: StackOrientation | null;
  /**
   * A star to put at the center of the view each time a picture is drawn. The place is a position of the
   * file, so the viewer finds it on the turned or redrawn picture. The caller gives the star of the
   * file that is on screen.
   */
  focusStar?: FocusStar | null;
  /**
   * A view to put back, for example when the person returns to this screen. It is applied once for each key,
   * as soon as a picture is drawn, and the picture stays hidden until then so the fitted view does not show first.
   */
  restoreView?: RestoreView | null;
  /** Called after the view changes (zoom or pan) once a picture is drawn, with where the view is now. */
  onViewChange?: (view: SavedViewport) => void;
  /** Called with true when the view is at the smallest zoom (the fit), and with false otherwise. */
  onMinimumZoomChange?: (atMinimum: boolean) => void;
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

export interface FitsRendererHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  zoomToFit: () => void;
  zoomToScale: (val: number) => void;
}

/**
 * Component to render FITS images with canvas interaction.
 * Interaction methods exposed via ref.
 */
const FitsRendererInternal = forwardRef<FitsRendererHandle, FitsRendererProps>((props, ref) => {
  const {
    imageUrl,
    imageBlob,
    frameInfo,
    loading: parentLoading,
    error: parentError,
    autoPanTrigger,
    disableStretch,
    displayRange,
    stretchParameters,
    overlayStars: overlayStarsAsSent,
    showOverlay,
    onStarClick,
    selectedStarId,
    selectedTarget,
    previewBlob = null,
    acceptPrefetch = false,
    showDetections = false,
    resolveMeasurePath,
    noPixelDataMessage = SET_DISPLAY_TO_LINEAR_MESSAGE,
    starProfilePictureNote,
    stackOrientation = null,
    focusStar = null,
    restoreView = null,
    onViewChange,
    onMinimumZoomChange,
  } = props;

  // Local State
  const [hoveredStarId, setHoveredStarId] = useState<string | null>(null);
  const [hoverPoint, setHoverPoint] = useState<{ x: number; y: number } | null>(null);
  // Where the pointer went down, to tell a click from a pan drag.
  const pressStartRef = useRef<{ x: number; y: number; starId: string | null } | null>(null);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const markerCanvasRef = useRef<HTMLCanvasElement>(null);
  const [drawnSize, setDrawnSize] = useState({ w: 0, h: 0 });

  const handleSetDrawnSize = useCallback((size: { w: number; h: number }) => {
    setDrawnSize((prev) => (prev.w === size.w && prev.h === size.h ? prev : size));
  }, []);

  // Hooks
  const { parsedData, bitmap, workerRef, phase, errorMessage, loadedKey } = useFitsLoader(
    imageUrl, imageBlob, undefined, acceptPrefetch
  );
  const previewBitmap = usePreviewBitmap(previewBlob);

  // True while the next change of drawn size is the preview being replaced by the full image.
  const keepViewOnResizeRef = useRef(false);

  const {
    zoom, panX, panY, atMinimumZoom,
    resetView, zoomBy, zoomToFit, zoomToScale, getCenterFraction, placeCenter,
    onPointerDown, onPointerMove, onPointerUp
  } = useCanvasInteraction(containerRef, drawnSize, undefined, keepViewOnResizeRef);

  // What is on the canvas, as reported by the drawer after each draw.
  const [drawnObject, setDrawnObject] = useState<ParsedFitsData | ImageBitmap | null>(null);
  const currentKey = imageBlob ?? imageUrl;
  // The full image is ready to draw only when it was made from the file now asked for.
  const mainReady = loadedKey !== null && loadedKey === currentKey && Boolean(parsedData || bitmap);
  const mainObject = mainReady ? (parsedData ?? bitmap) : null;
  const mainDrawn = mainObject !== null && drawnObject === mainObject;
  // The preview is drawn only until the full image is ready.
  const previewObject = !mainReady ? previewBitmap : null;
  const previewDrawn = previewObject !== null && drawnObject === previewObject;
  const firstFrameDrawn = mainDrawn || previewDrawn;

  // A raw frame is shown turned to match the stack; the file itself is never changed.
  const stackOrientationRef = useRef(stackOrientation);
  stackOrientationRef.current = stackOrientation;
  // A decoded FITS image knows its own header. A picture the backend drew (Auto Stretch, or the
  // preview) gets the same turn from the header of the file behind it, and waits for it.
  const bitmapTurn = useBitmapTurn(
    currentKey, stackOrientation, selectedTarget, resolveMeasurePath, !(mainReady && parsedData)
  );
  const turn: QuarterTurn = React.useMemo(
    () => (mainReady && parsedData ? turnToMatchStack(parsedData.header, stackOrientation) : bitmapTurn.turn),
    [mainReady, parsedData, stackOrientation, bitmapTurn.turn]
  );
  const bitmapTurnRef = useRef<QuarterTurn>(0);
  bitmapTurnRef.current = turn;
  // The size of the picture as shown: the file's width and height, swapped for a quarter turn.
  const shownSize = parsedData ? turnedSize(parsedData.w, parsedData.h, turn) : { w: 0, h: 0 };

  const previewShownRef = useRef(false);
  previewShownRef.current = previewDrawn;
  const previewBitmapRef = useRef<ImageBitmap | null>(null);
  previewBitmapRef.current = previewBitmap;
  // What was drawn last, so a different picture of the same target can take over the view.
  const lastImageRef = useRef<{ object: ParsedFitsData | ImageBitmap; target: string | null; isMain: boolean; geometry: ImageGeometry } | null>(null);
  // The point of the picture at the center of the view when it was replaced, and the sky position there.
  const carryRef = useRef<{ fraction: Fraction; sky: SkyPosition | null } | null>(null);
  // True when the picture drawn next belongs to another target and starts from a fresh fit.
  const freshFitRef = useRef(false);
  const targetRef = useRef(selectedTarget);
  targetRef.current = selectedTarget;
  const centerFractionRef = useRef(getCenterFraction);
  centerFractionRef.current = getCenterFraction;

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
      ? { width: drawn.w, height: drawn.h, header: drawn.header, turn: turnToMatchStack(drawn.header, stackOrientationRef.current) }
      : { width: drawn.width, height: drawn.height, turn: bitmapTurnRef.current };
    lastImageRef.current = { object: drawn, target, isMain: !isPreview, geometry };
    setDrawnObject(drawn);
  }, []);

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
  }, [currentKey]);

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
  }, [restoreView, firstFrameDrawn, drawnSize.w, drawnSize.h, placeCenter, shownSize.w]);
  // A view that cannot be put back (the picture never draws) must not leave the picture hidden.
  useEffect(() => {
    if (!restoreKey || !restoreHidesPicture) return undefined;
    const timer = window.setTimeout(() => setTimedOutRestoreKey(restoreKey), RESTORE_HIDE_LIMIT_MS);
    return () => window.clearTimeout(timer);
  }, [restoreKey, restoreHidesPicture]);

  // Tell the caller where the view is, so it can be saved and put back later. Nothing is sent while a saved view waits to be put back.
  const onViewChangeRef = useRef(onViewChange);
  onViewChangeRef.current = onViewChange;
  useEffect(() => {
    if (!firstFrameDrawn || restoreWaiting) return;
    const container = containerRef.current;
    const fitScale = container ? fitScaleFor(container.clientWidth, container.clientHeight, drawnSize.w, drawnSize.h) : 0;
    const center = getCenterFraction();
    if (!center || fitScale <= 0 || zoom <= 0) return;
    onViewChangeRef.current?.({ x: center.x, y: center.y, zoomOverFit: zoom / fitScale, turn });
  }, [firstFrameDrawn, restoreWaiting, zoom, panX, panY, drawnSize.w, drawnSize.h, turn, getCenterFraction]);

  // Disable automatic stretching for stacked images.
  const isStacked = disableStretch ?? (
    (imageUrl?.toLowerCase().includes('stacked') ?? false) ||
    (frameInfo?.objectId?.toLowerCase().includes('stacked') ?? false)
  );

  const effectiveStretch = props.stretch ?? !isStacked;

  // Automatically draw with AutoStretch (handled in worker for FITS)
  useCanvasDrawer(
    canvasRef, workerRef, mainReady ? parsedData : null,
    bitmapTurn.ready || (mainReady && parsedData) ? (mainReady ? bitmap : previewBitmap) : null,
    handleSetDrawnSize, effectiveStretch, displayRange, stretchParameters, handleDrawn, turn
  );

  // Click-to-measure: the panel's state, and the measurements behind it.
  const starProfile = useStarProfile(resolveMeasurePath);
  // What the panel says the image should draw: the cut line, or the two circles of the ring view.
  const [profileView, setProfileView] = useState<StarProfileView | null>(null);
  const closeStarProfile = starProfile.close;
  // A different image: a pinned star is followed to it once it is drawn (below); with no pinned star there is nothing to show.
  const hasPinRef = useRef(false);
  hasPinRef.current = starProfile.pin !== null;
  useEffect(() => {
    if (!hasPinRef.current) closeStarProfile();
  }, [currentKey, closeStarProfile]);

  // Tell the caller whether the view is at the smallest zoom, so it can disable its zoom-out control.
  const onMinimumZoomChangeRef = useRef(onMinimumZoomChange);
  onMinimumZoomChangeRef.current = onMinimumZoomChange;
  useEffect(() => {
    onMinimumZoomChangeRef.current?.(atMinimumZoom);
  }, [atMinimumZoom]);

  // Expose methods via REF
  useImperativeHandle(ref, () => ({
    zoomIn: () => zoomBy(1.2),
    zoomOut: () => zoomBy(0.8),
    zoomToFit: () => zoomToFit(),
    zoomToScale: (v: number) => zoomToScale(v)
  }));

  // Fit again only when the trigger itself changes. The reset function changes whenever the drawn
  // size does (a new display stretch draws the picture at another size), and that must not refit.
  const resetViewRef = useRef(resetView);
  resetViewRef.current = resetView;
  const lastAutoPanTriggerRef = useRef(autoPanTrigger);
  useEffect(() => {
    if (lastAutoPanTriggerRef.current === autoPanTrigger) return;
    lastAutoPanTriggerRef.current = autoPanTrigger;
    if (autoPanTrigger) resetViewRef.current();
  }, [autoPanTrigger]);

  // The astrometry stars are placed on the picture as the backend drew it; a turned picture turns them with it.
  const overlayStars = React.useMemo(() => {
    if (!overlayStarsAsSent || turn === 0) return overlayStarsAsSent;
    return overlayStarsAsSent.map((star) => {
      const ratio = window.devicePixelRatio || 1;
      const width = star.referenceWidth ?? parsedData?.w ?? (bitmap ? Math.round(bitmap.width / ratio) : 1);
      const height = star.referenceHeight ?? parsedData?.h ?? (bitmap ? Math.round(bitmap.height / ratio) : 1);
      const place = baseToTurned({ x: star.x, y: star.y }, width, height, turn);
      const shown = turnedSize(width, height, turn);
      return { ...star, x: place.x, y: place.y, referenceWidth: shown.w, referenceHeight: shown.h };
    });
  }, [overlayStarsAsSent, turn, parsedData, bitmap]);

  const refWidth = overlayStars && overlayStars[0]?.referenceWidth ? overlayStars[0].referenceWidth : (drawnSize.w || 1);
  const refHeight = overlayStars && overlayStars[0]?.referenceHeight ? overlayStars[0].referenceHeight : (drawnSize.h || 1);
  const effectiveZoom = zoom && zoom > 0 ? zoom : 1;
  const scaleX = (drawnSize.w > 0 && refWidth > 0) ? (drawnSize.w / refWidth) * effectiveZoom : effectiveZoom;
  const scaleY = (drawnSize.h > 0 && refHeight > 0) ? (drawnSize.h / refHeight) * effectiveZoom : effectiveZoom;
  const containerW = containerRef.current?.clientWidth || (drawnSize.w * effectiveZoom);
  const containerH = containerRef.current?.clientHeight || (drawnSize.h * effectiveZoom);

  // Image-to-screen mapping shared by the circles, the labels and the hover test.
  const screenTransform: StarScreenTransform = {
    scaleX, scaleY, panX, panY, viewWidth: containerW, viewHeight: containerH
  };
  // Star positions are FITS pixel positions, so the overlay waits for the full image and is not drawn on the preview.
  const overlayVisible = Boolean(showOverlay && mainDrawn && overlayStars && overlayStars.length > 0 && drawnSize.w > 0 && drawnSize.h > 0);

  // Only stars with a catalog name get a label; a stack's unmatched detections and missed catalog
  // stars are drawn as markers only.
  const labelableStars = React.useMemo(
    () => overlayStars?.filter((star) => !star.matchKind || star.matchKind === 'matched'),
    [overlayStars]
  );
  // The most important names in view are labeled, up to a cap that grows with the zoom.
  const labeledIds = React.useMemo(
    () => (overlayVisible && labelableStars
      ? chooseLabeledStarIds(
        labelableStars, { scaleX, scaleY, panX, panY, viewWidth: containerW, viewHeight: containerH },
        estimateBadgeWidth, undefined, (star) => isStarSelected(star, selectedStarId)
      )
      : new Set<string>()),
    [overlayVisible, labelableStars, scaleX, scaleY, panX, panY, containerW, containerH, selectedStarId]
  );

  // Draw one thin circle for every star on a canvas, which stays smooth with thousands of stars.
  // A layout effect runs before the browser paints, so the redrawn canvas lands in the same frame
  // in which the pan hook resets the layer's temporary drag transform.
  useLayoutEffect(() => {
    const canvas = markerCanvasRef.current;
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(containerW));
    const height = Math.max(1, Math.round(containerH));
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (!overlayVisible || !overlayStars) return;
    // The marker color is a theme token (`--accent-sky`), read through the layer's own custom property.
    const layerStyle = getComputedStyle(canvas);
    const markerColor = layerStyle.getPropertyValue('--star-marker-color').trim()
      || layerStyle.getPropertyValue('--accent-sky').trim();
    context.lineWidth = 1;
    context.globalAlpha = 0.65;
    const transform = { scaleX, scaleY, panX, panY, viewWidth: width, viewHeight: height };
    const overview = Math.min(scaleX, scaleY) < MARKER_ALL_STARS_SCALE;
    // Named stars and the target's catalog stars are circles; a detection with no catalog star is a
    // dashed circle; a catalog star with no detection is a cross. Each has its own theme color.
    const groups: Array<{ kind: 'named' | 'unmatched' | 'catalog_only'; color: string }> = [
      { kind: 'named', color: markerColor },
      { kind: 'unmatched', color: layerStyle.getPropertyValue('--star-marker-unmatched-color').trim() || markerColor },
      { kind: 'catalog_only', color: layerStyle.getPropertyValue('--star-marker-catalog-color').trim() || markerColor },
    ];
    for (const group of groups) {
      if (group.color) context.strokeStyle = group.color;
      context.setLineDash?.(group.kind === 'unmatched' ? [2, 2] : []);
      context.beginPath();
      for (const star of overlayStars) {
        const kind = star.matchKind === 'unmatched' || star.matchKind === 'catalog_only' ? star.matchKind : 'named';
        if (kind !== group.kind) continue;
        // Zoomed out, a named star shows a circle only when it has a name badge; the rest stay
        // findable by hovering. Unmatched and missed stars stay as smaller marks.
        if (overview && kind === 'named' && !labeledIds.has(star.id)) continue;
        const position = starScreenPosition(star, transform);
        if (!isInView(position, transform)) continue;
        const radius = overview && kind !== 'named' ? SMALL_MARKER_RADIUS_PX : MARKER_RADIUS_PX;
        if (kind === 'catalog_only') {
          const arm = radius * 0.8;
          context.moveTo(position.x - arm, position.y - arm);
          context.lineTo(position.x + arm, position.y + arm);
          context.moveTo(position.x - arm, position.y + arm);
          context.lineTo(position.x + arm, position.y - arm);
        } else {
          context.moveTo(position.x + radius, position.y);
          context.arc(position.x, position.y, radius, 0, Math.PI * 2);
        }
      }
      context.stroke();
    }
    context.setLineDash?.([]);
  }, [overlayVisible, overlayStars, labeledIds, scaleX, scaleY, panX, panY, containerW, containerH]);

  /**
   * Finds the star circle under the pointer, labeled or not, for the tooltip.
   * Pointers over a label badge are left to the badge's own handlers.
   *
   * @param event Pointer move event on the viewer container.
   */
  const handleHoverMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (!overlayVisible || !overlayStars || event.buttons !== 0) return;
    if ((event.target as Element).closest?.('.fits-renderer__star-group')) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const star = findStarNearPointer(overlayStars, screenTransform, x, y);
    setHoveredStarId(star ? star.id : null);
    setHoverPoint(star ? { x, y } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayVisible, overlayStars, scaleX, scaleY, panX, panY, containerW, containerH]);

  /**
   * Measures the star at a point of the viewer. The point becomes a file pixel with the same steps as
   * the pixel readout, and the worker names the pixel, so a click and the readout agree. An image
   * without decoded pixel values cannot say which pixel was clicked, so the panel says so and
   * nothing is sent to the backend.
   *
   * @param pointerX Horizontal position inside the viewer, in screen pixels.
   * @param pointerY Vertical position inside the viewer, in screen pixels.
   */
  /** The image the star profile was last measured or followed on, so each image is followed only once. */
  const followedKeyRef = useRef<unknown>(null);

  /**
   * Turns 6 screen pixels into image pixels at the current zoom.
   *
   * @param imageWidth Width of the picture as shown, in pixels.
   * @returns The search radius, from 3 to 50 image pixels.
   */
  const searchRadiusFor = (imageWidth: number): number => searchRadiusAtScale((drawnSize.w / imageWidth) * effectiveZoom);

  /**
   * Measures the star at one cell of the picture as shown and opens the star profile for it. A click and a
   * requested pick (`restoreView.pickStar`) both come here, so they open the same panel the same way.
   *
   * @param cell The cell of the picture as shown.
   * @param searchRadiusPx How far, in image pixels, the center may snap to a star.
   */
  const measureCell = async (cell: FileCell, searchRadiusPx: number) => {
    const worker = workerRef.current;
    if (!parsedData || !worker) return;
    // The reading names the file's own pixel; the probe (the picture's top-left base corner) tells the row order.
    const [reading, topRowReading] = await Promise.all([
      askWorkerForPixel(worker, parsedData.imageId, cell),
      askWorkerForPixel(worker, parsedData.imageId, rowOrderProbeCell(parsedData.w, parsedData.h, turn)),
    ]);
    if (!reading) return;
    followedKeyRef.current = currentKey;
    starProfile.measureAt({ x: reading.x, y: reading.y, firstRowAtTop: topRowReading?.y === 1, searchRadiusPx, turn });
  };

  const measureStarAt = async (pointerX: number, pointerY: number) => {
    const worker = workerRef.current;
    if (!mainDrawn || !parsedData || !worker) {
      // While the image is still loading there is nothing to say yet.
      if (!mainDrawn && (!firstFrameDrawn || previewDrawn)) return;
      followedKeyRef.current = currentKey;
      starProfile.showUnavailable(noPixelDataMessage);
      return;
    }
    const cell = pointerToFileCell(pointerX, pointerY, parsedData, drawnSize, effectiveZoom, panX, panY, turn);
    if (!cell) return;
    // The center may snap to a star within about 6 screen pixels of the click, so the search radius follows the zoom.
    await measureCell(cell, searchRadiusFor(shownSize.w));
  };

  // A view put back with `pickStar` picks the star at its center once the file's pixels are drawn, as a click there would.
  const pickedRestoreKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (!restoreView?.pickStar || appliedRestoreKey !== restoreView.key || !mainDrawn || !parsedData) return;
    if (pickedRestoreKeyRef.current === restoreView.key) return;
    pickedRestoreKeyRef.current = restoreView.key;
    const shown = turnedSize(parsedData.w, parsedData.h, turn);
    const cell: FileCell = {
      column: Math.min(shown.w - 1, Math.max(0, Math.floor(restoreView.view.x * shown.w))),
      rowFromTop: Math.min(shown.h - 1, Math.max(0, Math.floor(restoreView.view.y * shown.h))),
    };
    // The zoom the view was asked for, which the state may not show yet.
    const screenPixelsPerFilePixel = restoreView.scale ?? (drawnSize.w / shown.w) * effectiveZoom;
    void measureCell(cell, searchRadiusAtScale(screenPixelsPerFilePixel));
    // measureCell reads the newest image and settings when it runs; the pick is keyed by the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoreView, appliedRestoreKey, mainDrawn, parsedData]);

  // When another image is drawn while a star is pinned, find the star on it (once per image).
  const followProfile = starProfile.follow;
  const profileOpen = starProfile.state.status !== 'closed';
  useEffect(() => {
    if (!profileOpen || !hasPinRef.current || !mainDrawn) return;
    if (followedKeyRef.current === currentKey) return;
    followedKeyRef.current = currentKey;
    const worker = workerRef.current;
    if (!parsedData || !worker) {
      // The pinned star stays pinned; this picture just cannot be measured.
      starProfile.showUnavailable(noPixelDataMessage);
      return;
    }
    const firstCell = rowOrderProbeCell(parsedData.w, parsedData.h, turn);
    void askWorkerForPixel(worker, parsedData.imageId, firstCell).then((reading) => {
      void followProfile({ firstRowAtTop: reading?.y === 1, searchRadiusPx: searchRadiusFor(shownSize.w), turn });
    });
    // The search radius reads the zoom once, when the new image is first drawn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileOpen, mainDrawn, currentKey, parsedData, followProfile]);

  /**
   * Opens the star under the pointer when the press was a click and not a pan.
   * The star is a label badge under the pointer, or else the nearest circle.
   *
   * @param event Pointer up event on the viewer container.
   */
  const handleStarRelease = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = pressStartRef.current;
    pressStartRef.current = null;
    if (!start) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    const badgeStarId = start.starId
      ?? (event.target as Element).closest?.('[data-star-id]')?.getAttribute('data-star-id');
    let star = badgeStarId && overlayStars ? overlayStars.find((item) => item.id === badgeStarId) : undefined;
    if (!star && overlayVisible && overlayStars) {
      // Only a star the Astronomy Manager can open matters here, so look among those first.
      star = findStarNearPointer(
        overlayStars.filter(canOpenInAstronomyManager), screenTransform, event.clientX - rect.left, event.clientY - rect.top
      ) ?? undefined;
    }
    // A named star the Astronomy Manager can open still opens there; any other press measures a star.
    if (star && onStarClick && overlayVisible && canOpenInAstronomyManager(star)) {
      if (moved < STAR_CLICK_MOVE_THRESHOLD_PX) onStarClick(star);
      return;
    }
    // A press that did not move is a click and measures a star; a drag only panned.
    if (moved < STAR_CLICK_MOVE_THRESHOLD_PX) {
      void measureStarAt(event.clientX - rect.left, event.clientY - rect.top);
    }
  };

  // The marker of the measured star: a ring at the refined center and the line of the cut.
  const profileMarker = (() => {
    const profileState = starProfile.state;
    if (!parsedData || !mainDrawn || drawnSize.w <= 0 || drawnSize.h <= 0) return null;
    if (profileState.status !== 'ready' && profileState.status !== 'measuring') return null;
    const measurement = profileState.status === 'ready' ? profileState.measurement : profileState.previous;
    const firstRowAtTop = profileState.anchor.firstRowAtTop;
    const centerX = measurement ? measurement.center_x : profileState.anchor.x;
    const centerY = measurement ? measurement.center_y : profileState.anchor.y;
    // Screen pixels for one file pixel, along the screen's horizontal and vertical axes.
    const stepX = (drawnSize.w / shownSize.w) * effectiveZoom;
    const stepY = (drawnSize.h / shownSize.h) * effectiveZoom;
    /** Finds the screen position of a FITS pixel position, with the picture's row order and turn. */
    const screenPositionOf = (fitsX: number, fitsY: number) => {
      const place = fitsPixelToTurnedPicture(fitsX, fitsY, parsedData.w, parsedData.h, turn, firstRowAtTop);
      return { x: panX + place.x * stepX, y: panY + place.y * stepY };
    };
    const { x: screenX, y: screenY } = screenPositionOf(centerX, centerY);
    // The ring is one FWHM across (radius is half of it), in the size the picture is drawn at.
    const fittedWidth = measurement?.moffat.fitted ? measurement.moffat.fwhm_px : measurement?.gaussian.fitted ? measurement.gaussian.fwhm_px : null;
    const starFound = measurement ? measurement.star_found : true;
    // A profile that is not a star makes no width claim, so it gets a small marker of fixed size.
    const radius = starFound ? ((fittedWidth ?? 3) / 2) * stepX : NOT_A_STAR_MARKER_RADIUS_PX;
    let cut: { x1: number; y1: number; x2: number; y2: number } | null = null;
    let band: { inner: number; outer: number } | null = null;
    let neighbors: Array<{ x: number; y: number; radius: number }> = [];
    if (measurement && profileView?.mode === 'ring') {
      band = { inner: profileView.ringInnerPx * stepX, outer: profileView.ringOuterPx * stepX };
      // The neighbor stars the library left out of the ring, as circles of their mask radius.
      neighbors = measurement.ring_neighbors.map((neighbor) => ({
        ...screenPositionOf(neighbor.x, neighbor.y),
        radius: neighbor.mask_radius_px * stepX,
      }));
    } else if (measurement) {
      // The angle runs from +y (up a column) toward +x; screen rows grow downward when the first row is at the top.
      // A turned picture turns the cut with it.
      const angle = (starProfile.cutAngleDegrees * Math.PI) / 180;
      const halfLength = profileView?.cutHalfLengthPx ?? measurement.cut_half_length_px;
      const turnedHalfCut = turnStep(Math.sin(angle) * halfLength, Math.cos(angle) * halfLength * (firstRowAtTop ? 1 : -1), turn);
      const alongX = turnedHalfCut.x * stepX;
      const alongRows = turnedHalfCut.y * stepY;
      cut = { x1: screenX - alongX, y1: screenY - alongRows, x2: screenX + alongX, y2: screenY + alongRows };
    }
    return { screenX, screenY, radius, cut, band, neighbors, starFound };
  })();

  // The name of the measured star for the top of the profile panel: the overlay star at its center, or
  // "Unnamed star" with its sky position when the image is plate-solved.
  const profileStarName = React.useMemo(() => {
    const profileState = starProfile.state;
    if (profileState.status !== 'ready') return null;
    return nameStarAt(
      profileState.measurement.center_x,
      profileState.measurement.center_y,
      showOverlay ? overlayStarsAsSent : undefined,
      parsedData?.header ? createPixelToSky(parsedData.header) : null,
    );
  }, [starProfile.state, showOverlay, overlayStarsAsSent, parsedData]);

  // The overlay star at the measured center, when the catalog names it: its record fills the identity line.
  const profileCatalogStar = React.useMemo(() => {
    const profileState = starProfile.state;
    if (profileState.status !== 'ready' || !showOverlay) return null;
    const star = findStarAt(profileState.measurement.center_x, profileState.measurement.center_y, overlayStarsAsSent);
    return star && canOpenInAstronomyManager(star) ? star : null;
  }, [starProfile.state, showOverlay, overlayStarsAsSent]);
  const profileStarIdentity = useStarIdentity(profileCatalogStar?.libraryId ?? null);
  const openProfileStarInManager = React.useMemo(
    () => (profileCatalogStar && onStarClick ? () => onStarClick(profileCatalogStar) : undefined),
    [profileCatalogStar, onStarClick],
  );

  // The Detections overlay: fetched while it is on, drawn with the same placement as the star profile marker.
  // The profile panel reads the frame's typical star width from the same answer, so it asks for it while the panel is open.
  const profilePanelOpen = starProfile.state.status === 'ready';
  const detections = useDetections((showDetections || profilePanelOpen) && mainDrawn && parsedData !== null, currentKey, resolveMeasurePath);
  const frameStarWidthPx = detections.status === 'ready' ? detections.measurement.star_width_px ?? null : null;
  const [detectionsFirstRowAtTop, setDetectionsFirstRowAtTop] = useState<boolean | null>(null);
  useEffect(() => {
    const worker = workerRef.current;
    if (!showDetections || !mainDrawn || !parsedData || !worker) return;
    let cancelled = false;
    void askWorkerForPixel(worker, parsedData.imageId, rowOrderProbeCell(parsedData.w, parsedData.h, turn)).then((reading) => {
      if (!cancelled) setDetectionsFirstRowAtTop(reading ? reading.y === 1 : null);
    });
    return () => { cancelled = true; };
  }, [showDetections, mainDrawn, parsedData, workerRef, turn]);
  // Rebuilt only when the view or the detections change, not on every other re-render (hover, panel changes).
  const detectionPaths = React.useMemo(() => {
    if (!showDetections || detections.status !== 'ready' || detectionsFirstRowAtTop === null || !parsedData || !mainDrawn) return null;
    if (drawnSize.w <= 0 || drawnSize.h <= 0) return null;
    return buildDetectionPaths(detections.measurement.detections, {
      imageWidth: parsedData.w,
      imageHeight: parsedData.h,
      stepX: (drawnSize.w / shownSize.w) * effectiveZoom,
      stepY: (drawnSize.h / shownSize.h) * effectiveZoom,
      panX,
      panY,
      firstRowAtTop: detectionsFirstRowAtTop,
      turn,
      viewWidth: containerW,
      viewHeight: containerH,
      apertureRadiusPx: detections.measurement.aperture_radius_px,
    });
    // shownSize follows parsedData and turn, which are listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detections, detectionsFirstRowAtTop, parsedData, mainDrawn, drawnSize.w, drawnSize.h, effectiveZoom, panX, panY, containerW, containerH, turn]);
  const detectionsNote: string | null = !showDetections ? null
    : mainDrawn && !parsedData ? noPixelDataMessage
    : detections.status === 'loading' ? 'Finding stars…'
      : detections.status === 'error' ? detections.message
        : detections.status === 'ready' ? `${detections.measurement.detection_count.toLocaleString('en-US')} detections${detectionPaths?.mode === 'dots' ? '. Zoom in to see the photometry circles' : ''}`
          : null;

  const hoveredStar = hoveredStarId && overlayStars ? overlayStars.find((star) => star.id === hoveredStarId) : undefined;

  // An explicit error wins; otherwise the spinner stays until the first real draw
  // (the preview or the full image), whatever the loader is doing.
  const effectiveError = parentError || (phase === 'error' ? errorMessage : null);
  const effectiveLoading = !effectiveError && (!firstFrameDrawn || (parentLoading && !previewDrawn));

  return (
    <div className="fits-renderer">
      <div
        ref={containerRef}
        className={`fits-renderer__canvas-container${restoreHidesPicture ? ' fits-renderer__canvas-container--restoring' : ''}`}
        onPointerDown={(event) => {
          // The star group under the press is read now: pointer capture makes the release target the container.
          const pressedStarId = (event.target as Element).closest?.('[data-star-id]')?.getAttribute('data-star-id') ?? null;
          pressStartRef.current = { x: event.clientX, y: event.clientY, starId: pressedStarId };
          onPointerDown(event);
        }}
        onPointerMove={(event) => { onPointerMove(event); handleHoverMove(event); }}
        onPointerUp={(event) => { onPointerUp(event); handleStarRelease(event); }}
        onPointerLeave={(event) => { pressStartRef.current = null; onPointerUp(event); setHoveredStarId(null); setHoverPoint(null); }}
        style={{ touchAction: 'none', cursor: hoveredStarId ? 'pointer' : 'crosshair' }}
      >
        <canvas
          ref={canvasRef}
          className={`fits-renderer__canvas ${firstFrameDrawn ? '' : 'fits-renderer__canvas--hidden'}`}
        />
        {overlayVisible && (
          <canvas
            ref={markerCanvasRef}
            data-overlay-layer="true"
            className="fits-renderer__marker-layer"
            style={{ width: containerW, height: containerH }}
          />
        )}
        {overlayVisible && overlayStars && (
          <svg
            className="fits-renderer__overlay"
            data-overlay-layer="true"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              pointerEvents: 'none',
              overflow: 'hidden'
            }}
          >
            {/* Render unselected/unhovered stars first, hovered and selected stars last so they render on top */}
            {overlayStars
              .filter((star) => labeledIds.has(star.id))
              .sort((a, b) => {
                const aSelected = isStarSelected(a, selectedStarId);
                const bSelected = isStarSelected(b, selectedStarId);
                const aHovered = a.id === hoveredStarId;
                const bHovered = b.id === hoveredStarId;
                const aScore = (aSelected ? 2 : 0) + (aHovered ? 1 : 0);
                const bScore = (bSelected ? 2 : 0) + (bHovered ? 1 : 0);
                return aScore - bScore;
              })
              .map((star) => {
                const screenX = panX + star.x * scaleX;
                const screenY = panY + star.y * scaleY;
                const displayName = formatStarDisplayName(star.name);
                const nameLen = displayName.length;
                const badgeWidth = Math.max(56, nameLen * 7.6 + 26);
                const badgeHeight = 24;
                const badgeX = computeBadgePlacement(screenX, badgeWidth, containerW);
                const badgeY = -12;
                const isHovered = hoveredStarId === star.id;
                const isSelected = isStarSelected(star, selectedStarId);
                // Size the reticle to the detected star (falls back to the old fixed size).
                const measuredRadius = star.radiusPx ? star.radiusPx * Math.min(scaleX, scaleY) : 0;
                const ringRadius = measuredRadius > 0 ? Math.min(Math.max(measuredRadius + 4, 8), 60) : 15;
                const tickInner = ringRadius + 1;
                const tickOuter = ringRadius + 6;

                return (
                  <g
                    key={star.id}
                    className={`fits-renderer__star-group ${isHovered ? 'fits-renderer__star-group--hovered' : ''} ${isSelected ? 'fits-renderer__star-group--selected' : ''}`}
                    transform={`translate(${screenX}, ${screenY})`}
                    onPointerEnter={() => setHoveredStarId(star.id)}
                    onPointerLeave={() => setHoveredStarId(null)}
                    data-star-id={star.id}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        // Space must not scroll the page while it opens the star.
                        event.preventDefault();
                        if (canOpenInAstronomyManager(star)) onStarClick?.(star);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <title>{onStarClick && canOpenInAstronomyManager(star) ? `Open ${star.name} in the Astronomy Manager` : star.name}</title>

                    {/* Precision Reticle: Ring */}
                    <circle
                      cx={0}
                      cy={0}
                      r={ringRadius}
                      className="fits-renderer__reticle-ring"
                    />
                    {/* Precision Reticle: Cardinal Ticks */}
                    <line x1={0} y1={-tickOuter} x2={0} y2={-tickInner} className="fits-renderer__reticle-ticks" />
                    <line x1={0} y1={tickInner} x2={0} y2={tickOuter} className="fits-renderer__reticle-ticks" />
                    <line x1={-tickOuter} y1={0} x2={-tickInner} y2={0} className="fits-renderer__reticle-ticks" />
                    <line x1={tickInner} y1={0} x2={tickOuter} y2={0} className="fits-renderer__reticle-ticks" />
                    {/* Precision Reticle: Center Point */}
                    <circle cx={0} cy={0} r={1.5} className="fits-renderer__reticle-center" />

                    {/* Pill Badge Surface */}
                    <rect
                      x={badgeX}
                      y={badgeY}
                      width={badgeWidth}
                      height={badgeHeight}
                      rx={5}
                      ry={5}
                      className="fits-renderer__badge-bg"
                    />

                    {/* Star Display Name */}
                    <text
                      x={badgeX + 8}
                      y={0}
                      dominantBaseline="central"
                      className="fits-renderer__badge-name"
                    >
                      {displayName}
                    </text>

                    {/* Navigation Arrow */}
                    <text
                      x={badgeX + badgeWidth - 14}
                      y={0}
                      dominantBaseline="central"
                      className="fits-renderer__badge-action"
                    >
                      →
                    </text>
                  </g>
                );
              })}
          </svg>
        )}
        {showDetections && detectionPaths && (
          <svg className={`fits-renderer__detections${detectionPaths.mode === 'dots' ? ' fits-renderer__detections--dots' : ''}`} data-mode={detectionPaths.mode} data-overlay-layer="true" data-testid="detections-layer" aria-hidden="true">
            <path className="fits-renderer__detection" data-testid="detections-single" d={detectionPaths.circles} style={detectionPaths.mode === 'dots' ? { fillOpacity: detectionPaths.dotOpacity } : undefined} />
            <path className="fits-renderer__detection fits-renderer__detection--merged" data-testid="detections-merged" d={detectionPaths.diamonds} style={detectionPaths.mode === 'dots' ? { fillOpacity: detectionPaths.mergedDotOpacity } : undefined} />
          </svg>
        )}
        {profileMarker && (
          <svg className="fits-renderer__profile-marker" data-overlay-layer="true" data-testid="star-profile-marker" aria-hidden="true">
            {profileMarker.cut && (
              <line
                className="fits-renderer__profile-cut"
                x1={profileMarker.cut.x1}
                y1={profileMarker.cut.y1}
                x2={profileMarker.cut.x2}
                y2={profileMarker.cut.y2}
              />
            )}
            {profileMarker.neighbors.map((neighbor, index) => (
              <circle
                // The neighbors of one measurement are listed in a fixed order, so the index is a stable key.
                key={index}
                className="fits-renderer__profile-neighbor"
                data-testid="star-profile-neighbor"
                cx={neighbor.x}
                cy={neighbor.y}
                r={neighbor.radius}
              />
            ))}
            {profileMarker.band && (
              <>
                <circle className="fits-renderer__profile-band" data-testid="star-profile-band-inner" cx={profileMarker.screenX} cy={profileMarker.screenY} r={profileMarker.band.inner} />
                <circle className="fits-renderer__profile-band" data-testid="star-profile-band-outer" cx={profileMarker.screenX} cy={profileMarker.screenY} r={profileMarker.band.outer} />
              </>
            )}
            <circle
              className={`fits-renderer__profile-ring${!profileMarker.starFound ? ' fits-renderer__profile-ring--neutral' : profileMarker.radius < 4 ? ' fits-renderer__profile-ring--small' : ''}`}
              cx={profileMarker.screenX}
              cy={profileMarker.screenY}
              r={profileMarker.radius}
            >
              <title>{profileMarker.starFound ? 'Circle one FWHM across' : 'Center of the measurement'}</title>
            </circle>
          </svg>
        )}
        {hoveredStar && hoverPoint && !labeledIds.has(hoveredStar.id) && (
          <div
            className="fits-renderer__star-tooltip"
            role="tooltip"
            style={{ left: hoverPoint.x + 12, top: hoverPoint.y + 12 }}
          >
            {onStarClick && canOpenInAstronomyManager(hoveredStar) ? `Open ${hoveredStar.name} in the Astronomy Manager` : hoveredStar.name}
          </div>
        )}
      </div>

      {detectionsNote && (
        <div
          className={`fits-renderer__detections-note${detections.status === 'error' ? ' fits-renderer__detections-note--error' : ''}`}
          role="status"
          data-testid="detections-note"
          title="Circles or small dots are single stars; diamonds or larger dots are stars reported twice and merged."
        >
          {detectionsNote}
          {detections.status === 'ready' && detections.measurement.merged_count > 0 && ` (${detections.measurement.merged_count} merged, shown as ${detectionPaths?.mode === 'dots' ? 'larger dots' : 'diamonds'})`}
        </div>
      )}

      <PixelReadout
        containerRef={containerRef}
        workerRef={workerRef}
        parsedData={mainDrawn ? parsedData : null}
        drawnSize={drawnSize}
        zoom={effectiveZoom}
        panX={panX}
        panY={panY}
        turn={turn}
      />

      {mainDrawn && turn !== 0 && (
        <div className="fits-renderer__turn-note" data-testid="turn-note">
          <Badge
            opaque
            tone="info"
            title="This frame is stored the other way round from the stack, so the viewer draws it turned to match. The file is not changed, and pixel positions are the file's own."
          >
            {describeTurn(turn)}
          </Badge>
        </div>
      )}

      <StarProfilePanel
        state={starProfile.state}
        cutAngleDegrees={starProfile.cutAngleDegrees}
        onCutAngleChange={starProfile.setCutAngle}
        cutLengthPx={starProfile.cutLengthPx}
        onCutLengthChange={starProfile.setCutLength}
        onViewChange={setProfileView}
        followNote={starProfile.followNote}
        onClose={starProfile.close}
        pictureNote={starProfilePictureNote}
        starName={profileStarName}
        identity={profileStarIdentity}
        frameWidthPx={frameStarWidthPx}
        onOpenInManager={openProfileStarInManager}
      />

      {effectiveLoading && (
        <div className="fits-renderer__loading-overlay">
          <Skeleton variant="block" label="Loading the image" />
        </div>
      )}

      {/* The small preview is on screen while the full FITS image is still being made. */}
      {!effectiveError && previewDrawn && !mainDrawn && <UpdatingBar label="Loading the full image" />}

      {effectiveError && (
        <div className="fits-renderer__status error">
          {effectiveError}
        </div>
      )}
    </div>
  );
});

FitsRendererInternal.displayName = 'FitsRenderer';

export const FitsRenderer = React.memo(FitsRendererInternal, (prevProps, nextProps) => {
  return (
    prevProps.imageUrl === nextProps.imageUrl &&
    prevProps.imageBlob === nextProps.imageBlob &&
    prevProps.loading === nextProps.loading &&
    prevProps.error === nextProps.error &&
    prevProps.selectedTarget === nextProps.selectedTarget &&
    prevProps.autoPanTrigger === nextProps.autoPanTrigger &&
    prevProps.disableStretch === nextProps.disableStretch &&
    prevProps.stretch === nextProps.stretch &&
    prevProps.displayRange === nextProps.displayRange &&
    prevProps.stretchParameters === nextProps.stretchParameters &&
    prevProps.showOverlay === nextProps.showOverlay &&
    prevProps.overlayStars === nextProps.overlayStars &&
    prevProps.selectedStarId === nextProps.selectedStarId &&
    prevProps.previewBlob === nextProps.previewBlob &&
    prevProps.acceptPrefetch === nextProps.acceptPrefetch &&
    prevProps.showDetections === nextProps.showDetections &&
    prevProps.resolveMeasurePath === nextProps.resolveMeasurePath &&
    prevProps.noPixelDataMessage === nextProps.noPixelDataMessage &&
    prevProps.starProfilePictureNote === nextProps.starProfilePictureNote &&
    prevProps.stackOrientation === nextProps.stackOrientation &&
    prevProps.restoreView === nextProps.restoreView
  );
});
