/**
 * @file FitsRenderer.tsx
 * @description Renders FITS format and standard bitmap images onto an interactive HTML5 canvas.
 * Manages rendering worker delegation, zoom/pan transforms, and auto-stretch display.
 */
import React, { useState, useRef, useEffect, useLayoutEffect, useCallback, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader, ParsedFitsData } from './hooks/useFitsLoader';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';
import { usePreviewBitmap } from './hooks/usePreviewBitmap';
import { PixelReadout } from './PixelReadout';
import { StarProfilePanel, SET_DISPLAY_TO_LINEAR_MESSAGE } from './StarProfilePanel';
import type { StarProfileView } from './StarProfilePanel';
import { useStarProfile } from './hooks/useStarProfile';
import { useDetections } from './hooks/useDetections';
import { buildDetectionPaths } from './utils/detectionMarkers';
import { askWorkerForPixel, pointerToFileCell } from './utils/pixelQuery';
import { AstrometryOverlayStar } from '../services/astronomyService';
import { formatStarListLabel } from '../utils/starDisplayFormat';
import { Skeleton, UpdatingBar } from '../components/Skeleton';
import { StretchParameters } from '../types/backendTypes';
import {
  StarScreenTransform,
  chooseLabeledStarIds,
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
 * Compares primary identifiers, display names, and catalog substrings case-insensitively.
 *
 * @param star Astrometry overlay star item.
 * @param selectedId Target selected star identifier string.
 * @returns True if star matches the selection.
 */
export function isStarSelected(star: AstrometryOverlayStar, selectedId?: string | null): boolean {
  if (!selectedId) return false;
  const sId = selectedId.trim().toLowerCase();
  const starId = (star.id || '').trim().toLowerCase();
  const starName = (star.name || '').trim().toLowerCase();
  if (starId === sId || starName === sId) return true;
  if (starId && (sId.includes(starId) || starId.includes(sId))) return true;
  if (starName && (sId.includes(starName) || starName.includes(sId))) return true;

  // Compare formatted display names (e.g. "Cl* NGC 6205 KAD 656" vs "CI* NGC 6205 KAD 656" both normalize to "KAD 656")
  const normSelected = formatStarDisplayName(selectedId).trim().toLowerCase();
  const normStarName = formatStarDisplayName(star.name || star.id).trim().toLowerCase();
  if (normSelected && normStarName && normSelected === normStarName) return true;
  if (normSelected && normStarName && (normSelected.includes(normStarName) || normStarName.includes(normSelected))) return true;

  return false;
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

/** How far from a click, in screen pixels, a measurement may look for the star's center. */
export const STAR_SEARCH_SCREEN_PX = 6;

/** Smallest search radius sent to the backend, in image pixels. */
export const STAR_SEARCH_MIN_PX = 3;

/** Largest search radius sent to the backend, in image pixels (the backend caps it at 50 too). */
export const STAR_SEARCH_MAX_PX = 50;

/** Radius, in screen pixels, of the small marker drawn when the profile does not look like a star. */
const NOT_A_STAR_MARKER_RADIUS_PX = 4;

/** Most the pointer may move, in screen pixels, between press and release for the gesture to count as a click and not a pan. */
export const STAR_CLICK_MOVE_THRESHOLD_PX = 4;

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
}

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
    overlayStars,
    showOverlay,
    onStarClick,
    selectedStarId,
    previewBlob = null,
    acceptPrefetch = false,
    showDetections = false,
    resolveMeasurePath,
    noPixelDataMessage = SET_DISPLAY_TO_LINEAR_MESSAGE,
    starProfilePictureNote
  } = props;

  // Local State
  const [hoveredStarId, setHoveredStarId] = useState<string | null>(null);
  const [hoverPoint, setHoverPoint] = useState<{ x: number; y: number } | null>(null);
  // Where the pointer went down, to tell a click from a pan drag.
  const pressStartRef = useRef<{ x: number; y: number } | null>(null);

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
    zoom, panX, panY,
    resetView, zoomBy, zoomToFit, zoomToScale,
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

  const previewShownRef = useRef(false);
  previewShownRef.current = previewDrawn;
  const previewBitmapRef = useRef<ImageBitmap | null>(null);
  previewBitmapRef.current = previewBitmap;
  const handleDrawn = useCallback((drawn: ParsedFitsData | ImageBitmap) => {
    // Replacing the preview with the full image: keep the zoom and position.
    if (drawn !== previewBitmapRef.current && previewShownRef.current) keepViewOnResizeRef.current = true;
    setDrawnObject(drawn);
  }, []);

  // A different image starts from a fresh fit.
  useEffect(() => {
    keepViewOnResizeRef.current = false;
  }, [currentKey]);

  // Disable automatic stretching for stacked images.
  const isStacked = disableStretch ?? (
    (imageUrl?.toLowerCase().includes('stacked') ?? false) ||
    (frameInfo?.objectId?.toLowerCase().includes('stacked') ?? false)
  );

  const effectiveStretch = props.stretch ?? !isStacked;

  // Automatically draw with AutoStretch (handled in worker for FITS)
  useCanvasDrawer(
    canvasRef, workerRef, mainReady ? parsedData : null, mainReady ? bitmap : previewBitmap,
    handleSetDrawnSize, effectiveStretch, displayRange, stretchParameters, handleDrawn
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

  // Expose methods via REF
  useImperativeHandle(ref, () => ({
    zoomIn: () => zoomBy(1.2),
    zoomOut: () => zoomBy(0.8),
    zoomToFit: () => zoomToFit(),
    zoomToScale: (v: number) => zoomToScale(v)
  }));

  // React to autoPanTrigger
  useEffect(() => {
    if (autoPanTrigger) resetView();
  }, [autoPanTrigger, resetView]);

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

  // A fixed label budget is spent on the stars in view, so zooming in shows more labels per area.
  const labeledIds = React.useMemo(
    () => (overlayVisible && overlayStars
      ? chooseLabeledStarIds(
        overlayStars, { scaleX, scaleY, panX, panY, viewWidth: containerW, viewHeight: containerH },
        estimateBadgeWidth, undefined, (star) => isStarSelected(star, selectedStarId)
      )
      : new Set<string>()),
    [overlayVisible, overlayStars, scaleX, scaleY, panX, panY, containerW, containerH, selectedStarId]
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
    if (markerColor) context.strokeStyle = markerColor;
    context.lineWidth = 1;
    context.globalAlpha = 0.65;
    context.beginPath();
    const transform = { scaleX, scaleY, panX, panY, viewWidth: width, viewHeight: height };
    for (const star of overlayStars) {
      const position = starScreenPosition(star, transform);
      if (!isInView(position, transform)) continue;
      context.moveTo(position.x + MARKER_RADIUS_PX, position.y);
      context.arc(position.x, position.y, MARKER_RADIUS_PX, 0, Math.PI * 2);
    }
    context.stroke();
  }, [overlayVisible, overlayStars, scaleX, scaleY, panX, panY, containerW, containerH]);

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
   * @param imageWidth Width of the decoded image, in pixels.
   * @returns The search radius, from 3 to 50 image pixels.
   */
  const searchRadiusFor = (imageWidth: number): number => {
    const screenPixelsPerFilePixel = (drawnSize.w / imageWidth) * effectiveZoom;
    return Math.min(STAR_SEARCH_MAX_PX, Math.max(STAR_SEARCH_MIN_PX, STAR_SEARCH_SCREEN_PX / screenPixelsPerFilePixel));
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
    const cell = pointerToFileCell(pointerX, pointerY, parsedData, drawnSize, effectiveZoom, panX, panY);
    if (!cell) return;
    const [reading, topRowReading] = await Promise.all([
      askWorkerForPixel(worker, parsedData.imageId, cell),
      askWorkerForPixel(worker, parsedData.imageId, { column: cell.column, rowFromTop: 0 }),
    ]);
    if (!reading) return;
    // The center may snap to a star within about 6 screen pixels of the click, so the search radius follows the zoom.
    const searchRadiusPx = searchRadiusFor(parsedData.w);
    followedKeyRef.current = currentKey;
    starProfile.measureAt({ x: reading.x, y: reading.y, firstRowAtTop: topRowReading?.y === 1, searchRadiusPx });
  };

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
    const firstCell = { column: 0, rowFromTop: 0 };
    void askWorkerForPixel(worker, parsedData.imageId, firstCell).then((reading) => {
      void followProfile({ firstRowAtTop: reading?.y === 1, searchRadiusPx: searchRadiusFor(parsedData.w) });
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
    if (start && !(event.target as Element).closest?.('[data-star-id]')) {
      const rect = event.currentTarget.getBoundingClientRect();
      // A named star's circle (with the astrometry overlay on) still opens the star in the Astronomy Manager.
      const onNamedCircle = Boolean(onStarClick && overlayVisible && overlayStars
        && findStarNearPointer(overlayStars, screenTransform, event.clientX - rect.left, event.clientY - rect.top));
      if (!onNamedCircle) {
        // A press that did not move is a click and measures a star; a drag only panned.
        if (Math.hypot(event.clientX - start.x, event.clientY - start.y) < STAR_CLICK_MOVE_THRESHOLD_PX) {
          void measureStarAt(event.clientX - rect.left, event.clientY - rect.top);
        }
        return;
      }
    }
    if (!start || !onStarClick || !overlayVisible || !overlayStars) return;
    const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    if (moved >= STAR_CLICK_MOVE_THRESHOLD_PX) return;
    const badgeStarId = (event.target as Element).closest?.('[data-star-id]')?.getAttribute('data-star-id');
    let star = badgeStarId ? overlayStars.find((item) => item.id === badgeStarId) : undefined;
    if (!star) {
      const rect = event.currentTarget.getBoundingClientRect();
      star = findStarNearPointer(
        overlayStars, screenTransform, event.clientX - rect.left, event.clientY - rect.top
      ) ?? undefined;
    }
    if (star) onStarClick(star);
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
    // Screen pixels for one file pixel, along the columns and along the rows.
    const stepX = (drawnSize.w / parsedData.w) * effectiveZoom;
    const stepY = (drawnSize.h / parsedData.h) * effectiveZoom;
    const rowFromTop = firstRowAtTop ? centerY - 0.5 : parsedData.h - centerY + 0.5;
    const screenX = panX + (centerX - 0.5) * stepX;
    const screenY = panY + rowFromTop * stepY;
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
        x: panX + (neighbor.x - 0.5) * stepX,
        y: panY + (firstRowAtTop ? neighbor.y - 0.5 : parsedData.h - neighbor.y + 0.5) * stepY,
        radius: neighbor.mask_radius_px * stepX,
      }));
    } else if (measurement) {
      // The angle runs from +y (up a column) toward +x; screen rows grow downward when the first row is at the top.
      const angle = (starProfile.cutAngleDegrees * Math.PI) / 180;
      const alongX = Math.sin(angle) * (profileView?.cutHalfLengthPx ?? measurement.cut_half_length_px) * stepX;
      const alongRows = Math.cos(angle) * (profileView?.cutHalfLengthPx ?? measurement.cut_half_length_px) * stepY * (firstRowAtTop ? 1 : -1);
      cut = { x1: screenX - alongX, y1: screenY - alongRows, x2: screenX + alongX, y2: screenY + alongRows };
    }
    return { screenX, screenY, radius, cut, band, neighbors, starFound };
  })();

  // The Detections overlay: fetched while it is on, drawn with the same placement as the star profile marker.
  const detections = useDetections(showDetections && mainDrawn && parsedData !== null, currentKey, resolveMeasurePath);
  const [detectionsFirstRowAtTop, setDetectionsFirstRowAtTop] = useState<boolean | null>(null);
  useEffect(() => {
    const worker = workerRef.current;
    if (!showDetections || !mainDrawn || !parsedData || !worker) return;
    let cancelled = false;
    void askWorkerForPixel(worker, parsedData.imageId, { column: 0, rowFromTop: 0 }).then((reading) => {
      if (!cancelled) setDetectionsFirstRowAtTop(reading ? reading.y === 1 : null);
    });
    return () => { cancelled = true; };
  }, [showDetections, mainDrawn, parsedData, workerRef]);
  // Rebuilt only when the view or the detections change, not on every other re-render (hover, panel changes).
  const detectionPaths = React.useMemo(() => {
    if (detections.status !== 'ready' || detectionsFirstRowAtTop === null || !parsedData || !mainDrawn) return null;
    if (drawnSize.w <= 0 || drawnSize.h <= 0) return null;
    return buildDetectionPaths(detections.measurement.detections, {
      imageWidth: parsedData.w,
      imageHeight: parsedData.h,
      stepX: (drawnSize.w / parsedData.w) * effectiveZoom,
      stepY: (drawnSize.h / parsedData.h) * effectiveZoom,
      panX,
      panY,
      firstRowAtTop: detectionsFirstRowAtTop,
      viewWidth: containerW,
      viewHeight: containerH,
      apertureRadiusPx: detections.measurement.aperture_radius_px,
    });
  }, [detections, detectionsFirstRowAtTop, parsedData, mainDrawn, drawnSize.w, drawnSize.h, effectiveZoom, panX, panY, containerW, containerH]);
  const detectionsNote: string | null = !showDetections ? null
    : mainDrawn && !parsedData ? noPixelDataMessage
    : detections.status === 'loading' ? 'Finding stars…'
      : detections.status === 'error' ? detections.message
        : detections.status === 'ready' ? `${detections.measurement.detection_count.toLocaleString('en-US')} detections`
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
        className="fits-renderer__canvas-container"
        onPointerDown={(event) => { pressStartRef.current = { x: event.clientX, y: event.clientY }; onPointerDown(event); }}
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
                        onStarClick?.(star);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <title>{`Open ${star.name} in the Astronomy Manager`}</title>

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
          <svg className="fits-renderer__detections" data-overlay-layer="true" data-testid="detections-layer" aria-hidden="true">
            <path className="fits-renderer__detection" data-testid="detections-single" d={detectionPaths.circles} />
            <path className="fits-renderer__detection fits-renderer__detection--merged" data-testid="detections-merged" d={detectionPaths.diamonds} />
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
            {`Open ${hoveredStar.name} in the Astronomy Manager`}
          </div>
        )}
      </div>

      {detectionsNote && (
        <div
          className={`fits-renderer__detections-note${detections.status === 'error' ? ' fits-renderer__detections-note--error' : ''}`}
          role="status"
          data-testid="detections-note"
          title="Circles are single stars; diamonds are stars reported twice and merged."
        >
          {detectionsNote}
          {detections.status === 'ready' && detections.measurement.merged_count > 0 && ` (${detections.measurement.merged_count} merged, shown as diamonds)`}
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
      />

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
    prevProps.starProfilePictureNote === nextProps.starProfilePictureNote
  );
});
