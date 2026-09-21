/**
 * @file FitsRenderer.tsx
 * @description Renders FITS format and standard bitmap images onto an interactive HTML5 canvas.
 * Manages rendering worker delegation, zoom/pan transforms, and auto-stretch display.
 */
import React, { useState, useRef, useEffect, useCallback, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader } from './hooks/useFitsLoader';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';
import { AstrometryOverlayStar } from '../services/astronomyService';
import { formatStarListLabel } from '../../astronomyDisplay/utils/starDisplayFormat';

/**
 * Shortens long catalog identifiers for HUD reticle badges.
 *
 * Compresses verbose coordinate substrings from common surveys (2MASS, GPM, SDSS)
 * while preserving distinct astronomical identifiers, and delegating to standard
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
  // 2MASS coordinate compression: 2MASS J16413289+3626285 -> 2MASS 1641+3626
  const massMatch = name.match(/2MASS\s*J?([0-9]{4})[0-9]*([+-][0-9]{4})[0-9]*/i);
  if (massMatch) {
    return `2MASS ${massMatch[1]}${massMatch[2]}`;
  }
  // GPM coordinate compression: GPM 258.328551+36.535702 -> GPM 258+36
  const gpmMatch = name.match(/GPM\s*([0-9]+)\.[0-9]+([+-][0-9]+)\.[0-9]*/i);
  if (gpmMatch) {
    return `GPM ${gpmMatch[1]}${gpmMatch[2]}`;
  }
  // SDSS coordinate compression: SDSS J164132.89+362628.5 -> SDSS 1641+3626
  const sdssMatch = name.match(/SDSS\s*J?([0-9]{4})[0-9.]*([+-][0-9]{4})[0-9.]*/i);
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
  overlayStars?: AstrometryOverlayStar[];
  showOverlay?: boolean;
  onStarClick?: (star: AstrometryOverlayStar) => void;
  selectedStarId?: string | null;
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
    overlayStars,
    showOverlay,
    onStarClick,
    selectedStarId
  } = props;

  // Local State
  const [status, setStatus] = useState<string | null>(null);
  const [hoveredStarId, setHoveredStarId] = useState<string | null>(null);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [drawnSize, setDrawnSize] = useState({ w: 0, h: 0 });

  const handleSetDrawnSize = useCallback((size: { w: number; h: number }) => {
    setDrawnSize((prev) => (prev.w === size.w && prev.h === size.h ? prev : size));
  }, []);

  // Hooks
  const { parsedData, bitmap } = useFitsLoader(imageUrl, imageBlob, setStatus);

  const {
    zoom, panX, panY,
    resetView, zoomBy, zoomToFit, zoomToScale,
    onPointerDown, onPointerMove, onPointerUp
  } = useCanvasInteraction(containerRef, drawnSize);

  // REQ: IMG-3.6: Disable automatic stretching for stacked images.
  const isStacked = disableStretch ?? (
    (imageUrl?.toLowerCase().includes('stacked') ?? false) ||
    (frameInfo?.objectId?.toLowerCase().includes('stacked') ?? false)
  );

  const effectiveStretch = props.stretch ?? !isStacked;

  // Automatically draw with AutoStretch (handled in worker for FITS)
  useCanvasDrawer(canvasRef, parsedData, bitmap, handleSetDrawnSize, effectiveStretch);

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

  const effectiveLoading = parentLoading || status?.startsWith('Loading');
  const effectiveError = parentError || (status?.startsWith('Error') ? status : null);

  return (
    <div className="fits-renderer">
      <div
        ref={containerRef}
        className="fits-renderer__canvas-container"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        style={{ touchAction: 'none' }}
      >
        <canvas
          ref={canvasRef}
          className={`fits-renderer__canvas ${effectiveLoading ? 'fits-renderer__canvas--loading' : ''}`}
        />
        {showOverlay && overlayStars && overlayStars.length > 0 && drawnSize.w > 0 && drawnSize.h > 0 && (
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
            {[...overlayStars]
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
                const specText = (star.spectralType && star.spectralType !== 'Unknown') ? star.spectralType : '';
                const hasSpec = specText.length > 0;
                const nameLen = displayName.length;
                const badgeWidth = Math.max(56, nameLen * 7.6 + (hasSpec ? specText.length * 6.8 + 14 : 0) + 26);
                const badgeHeight = 24;
                const badgeX = computeBadgePlacement(screenX, badgeWidth, containerW);
                const badgeY = -12;
                const isHovered = hoveredStarId === star.id;
                const isSelected = isStarSelected(star, selectedStarId);

                return (
                  <g
                    key={star.id}
                    className={`fits-renderer__star-group ${isHovered ? 'fits-renderer__star-group--hovered' : ''} ${isSelected ? 'fits-renderer__star-group--selected' : ''}`}
                    transform={`translate(${screenX}, ${screenY})`}
                    onPointerEnter={() => setHoveredStarId(star.id)}
                    onPointerLeave={() => setHoveredStarId(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStarClick?.(star);
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <title>{`${star.name} (${star.spectralType || 'Unknown'}) - Click to inspect in Astronomy Manager`}</title>

                    {/* Precision Reticle: Ring */}
                    <circle
                      cx={0}
                      cy={0}
                      r={15}
                      className="fits-renderer__reticle-ring"
                    />
                    {/* Precision Reticle: Cardinal Ticks */}
                    <line x1={0} y1={-21} x2={0} y2={-16} className="fits-renderer__reticle-ticks" />
                    <line x1={0} y1={16} x2={0} y2={21} className="fits-renderer__reticle-ticks" />
                    <line x1={-21} y1={0} x2={-16} y2={0} className="fits-renderer__reticle-ticks" />
                    <line x1={16} y1={0} x2={21} y2={0} className="fits-renderer__reticle-ticks" />
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

                    {/* Spectral Type Tag */}
                    {hasSpec && (
                      <text
                        x={badgeX + 8 + nameLen * 7.6 + 5}
                        y={0}
                        dominantBaseline="central"
                        className="fits-renderer__badge-type"
                      >
                        {specText}
                      </text>
                    )}

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
      </div>

      {effectiveLoading && (
        <div className="fits-renderer__loading-overlay">
          <div className="fits-renderer__spinner" />
          <div className="fits-renderer__loading-text">Loading Image...</div>
        </div>
      )}

      {status && !effectiveLoading && (
        <div className="fits-renderer__status">
          {status}
        </div>
      )}
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
    prevProps.showOverlay === nextProps.showOverlay &&
    prevProps.overlayStars === nextProps.overlayStars &&
    prevProps.selectedStarId === nextProps.selectedStarId
  );
});
