/**
 * @file useStarOverlay.ts
 * @description The astrometry star overlay of the FITS viewer: places the overlay stars on the
 * picture as it is shown (turned with it), chooses which stars get a name label, draws a thin
 * circle on every star on a canvas, and finds the star under the pointer for the tooltip.
 */

import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ParsedFitsData } from './useFitsLoader';
import { baseToTurned, turnedSize } from '../displayTurn';
import type { QuarterTurn } from '../displayTurn';
import type { AstrometryOverlayStar } from '../../services/astronomyService';
import {
  StarScreenTransform,
  chooseLabeledStarIds,
  estimateBadgeWidth,
  findStarNearPointer,
  isInView,
  isStarSelected,
  MARKER_ALL_STARS_SCALE,
  starScreenPosition,
} from '../utils/starOverlayLayout';

/** Radius, in screen pixels, of the thin circle drawn on every star. */
const MARKER_RADIUS_PX = 5;

/** Radius, in screen pixels, of the smaller marks for unmatched and missed stars when zoomed out. */
const SMALL_MARKER_RADIUS_PX = 3;

/** What `useStarOverlay` needs from the viewer. */
export interface StarOverlayInput {
  /** The overlay stars as the backend placed them, on the picture it drew. */
  overlayStarsAsSent: AstrometryOverlayStar[] | undefined;
  /** True while the overlay is switched on. */
  showOverlay: boolean;
  /** True once the full image is on the canvas; star positions are FITS pixels, so the preview gets none. */
  mainDrawn: boolean;
  /** The selected star's id; it always gets a label. */
  selectedStarId: string | null | undefined;
  /** The quarter turn the picture is shown with. */
  turn: QuarterTurn;
  /** The decoded FITS data, or null for a bitmap. */
  parsedData: ParsedFitsData | null;
  /** The decoded bitmap, or null for FITS data. */
  bitmap: ImageBitmap | null;
  /** The size the picture is drawn at on the canvas. */
  drawnSize: { w: number; h: number };
  /** The zoom, in screen pixels per drawn pixel (never 0). */
  effectiveZoom: number;
  /** The picture's offset on screen. */
  panX: number;
  /** The picture's offset on screen. */
  panY: number;
  /** The viewer's width on screen. */
  containerW: number;
  /** The viewer's height on screen. */
  containerH: number;
}

/** What `useStarOverlay` gives back. */
export interface StarOverlay {
  /** The overlay stars placed on the picture as it is shown. */
  overlayStars: AstrometryOverlayStar[] | undefined;
  /** True when the overlay is drawn. */
  overlayVisible: boolean;
  /** Screen pixels per overlay pixel, across. */
  scaleX: number;
  /** Screen pixels per overlay pixel, down. */
  scaleY: number;
  /** The image-to-screen mapping shared by the circles, the labels and the hover test. */
  screenTransform: StarScreenTransform;
  /** The ids of the stars that get a name label. */
  labeledIds: Set<string>;
  /** The canvas the star circles are drawn on. */
  markerCanvasRef: React.RefObject<HTMLCanvasElement | null>;
  /** The star under the pointer, or null. */
  hoveredStarId: string | null;
  /** Sets the star under the pointer (a label badge's own handlers use it). */
  setHoveredStarId: (id: string | null) => void;
  /** Where the pointer is over a star circle, for the tooltip, or null. */
  hoverPoint: { x: number; y: number } | null;
  /** The star under the pointer, if any. */
  hoveredStar: AstrometryOverlayStar | undefined;
  /** Finds the star circle under the pointer as it moves. */
  handleHoverMove: (event: React.PointerEvent<HTMLDivElement>) => void;
  /** Forgets the star under the pointer (the pointer left the viewer). */
  clearHover: () => void;
}

/**
 * Places and draws the astrometry star overlay and follows the pointer over it.
 *
 * @param input The overlay stars and the view (see `StarOverlayInput`).
 * @returns The placed stars, the labels and the hover state (see `StarOverlay`).
 */
export function useStarOverlay(input: StarOverlayInput): StarOverlay {
  const {
    overlayStarsAsSent, showOverlay, mainDrawn, selectedStarId, turn, parsedData, bitmap,
    drawnSize, effectiveZoom, panX, panY, containerW, containerH,
  } = input;
  const [hoveredStarId, setHoveredStarId] = useState<string | null>(null);
  const [hoverPoint, setHoverPoint] = useState<{ x: number; y: number } | null>(null);
  const markerCanvasRef = useRef<HTMLCanvasElement>(null);

  // The astrometry stars are placed on the picture as the backend drew it; a turned picture turns them with it.
  const overlayStars = useMemo(() => {
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
  const scaleX = (drawnSize.w > 0 && refWidth > 0) ? (drawnSize.w / refWidth) * effectiveZoom : effectiveZoom;
  const scaleY = (drawnSize.h > 0 && refHeight > 0) ? (drawnSize.h / refHeight) * effectiveZoom : effectiveZoom;

  // Image-to-screen mapping shared by the circles, the labels and the hover test.
  const screenTransform: StarScreenTransform = {
    scaleX, scaleY, panX, panY, viewWidth: containerW, viewHeight: containerH
  };
  // Star positions are FITS pixel positions, so the overlay waits for the full image and is not drawn on the preview.
  const overlayVisible = Boolean(showOverlay && mainDrawn && overlayStars && overlayStars.length > 0 && drawnSize.w > 0 && drawnSize.h > 0);

  // Only stars with a catalog name get a label; a stack's unmatched detections and missed catalog
  // stars are drawn as markers only.
  const labelableStars = useMemo(
    () => overlayStars?.filter((star) => !star.matchKind || star.matchKind === 'matched'),
    [overlayStars]
  );
  // The most important names in view are labeled, up to a cap that grows with the zoom.
  const labeledIds = useMemo(
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

  /** Forgets the star under the pointer. */
  const clearHover = useCallback(() => {
    setHoveredStarId(null);
    setHoverPoint(null);
  }, []);

  const hoveredStar = hoveredStarId && overlayStars ? overlayStars.find((star) => star.id === hoveredStarId) : undefined;

  return {
    overlayStars, overlayVisible, scaleX, scaleY, screenTransform, labeledIds, markerCanvasRef,
    hoveredStarId, setHoveredStarId, hoverPoint, hoveredStar, handleHoverMove, clearHover,
  };
}
