/**
 * @file FitsRenderer.tsx
 * @description Renders FITS format and standard bitmap images onto an interactive HTML5 canvas.
 * Manages rendering worker delegation, zoom/pan transforms, and auto-stretch display.
 */
import React, { useState, useRef, useEffect, useCallback, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader, ParsedFitsData } from './hooks/useFitsLoader';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';
import { usePreviewBitmap } from './hooks/usePreviewBitmap';
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
    acceptPrefetch = false
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

  // REQ: IMG-3.6: Disable automatic stretching for stacked images.
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
  useEffect(() => {
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
    context.strokeStyle = getComputedStyle(canvas).color || '#33c7de';
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
   * Opens the star under the pointer when the press was a click and not a pan.
   * The star is a label badge under the pointer, or else the nearest circle.
   *
   * @param event Pointer up event on the viewer container.
   */
  const handleStarRelease = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = pressStartRef.current;
    pressStartRef.current = null;
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
        style={{ touchAction: 'none', cursor: hoveredStarId ? 'pointer' : undefined }}
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
    prevProps.acceptPrefetch === nextProps.acceptPrefetch
  );
});
