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
    onStarClick
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
    onPointerDown, onPointerMove, onPointerUp,
    scheduleTransformWrite
  } = useCanvasInteraction(containerRef, drawnSize);

  // Sync transform to overlay whenever overlay is toggled on or size changes
  useEffect(() => {
    if (showOverlay) {
      scheduleTransformWrite();
    }
  }, [showOverlay, scheduleTransformWrite, drawnSize]);

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

  const invScale = zoom && zoom > 0 ? 1 / zoom : 1;

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
            viewBox={`0 0 ${overlayStars[0]?.referenceWidth || drawnSize.w} ${overlayStars[0]?.referenceHeight || drawnSize.h}`}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: `${drawnSize.w}px`,
              height: `${drawnSize.h}px`,
              pointerEvents: 'none',
              transformOrigin: '0 0',
              overflow: 'visible'
            }}
          >
            {/* Render unhovered stars first, hovered star last so it renders on top */}
            {[...overlayStars]
              .sort((a, b) => (a.id === hoveredStarId ? 1 : b.id === hoveredStarId ? -1 : 0))
              .map((star, index) => {
                const displayName = formatStarDisplayName(star.name);
                const specText = (star.spectralType && star.spectralType !== 'Unknown') ? star.spectralType : '';
                const hasSpec = specText.length > 0;
                const nameLen = displayName.length;
                const badgeWidth = Math.max(54, nameLen * 7.2 + (hasSpec ? specText.length * 6.5 + 14 : 0) + 24);
                const badgeHeight = 22;
                const refWidth = overlayStars[0]?.referenceWidth || drawnSize.w;
                const isNearRightEdge = star.x > (refWidth - 160);
                const isNearLeftEdge = star.x < 160;
                const placeLeft = isNearRightEdge || (!isNearLeftEdge && (index % 2 === 1));
                const badgeX = placeLeft ? -badgeWidth - 18 : 18;
                const badgeY = -11;
                const isHovered = hoveredStarId === star.id;

                return (
                  <g
                    key={star.id}
                    className={`fits-renderer__star-group ${isHovered ? 'fits-renderer__star-group--hovered' : ''}`}
                    transform={`translate(${star.x}, ${star.y}) scale(${invScale})`}
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
                      r={14}
                      className="fits-renderer__reticle-ring"
                    />
                    {/* Precision Reticle: Cardinal Ticks */}
                    <line x1={0} y1={-19} x2={0} y2={-14} className="fits-renderer__reticle-ticks" />
                    <line x1={0} y1={14} x2={0} y2={19} className="fits-renderer__reticle-ticks" />
                    <line x1={-19} y1={0} x2={-14} y2={0} className="fits-renderer__reticle-ticks" />
                    <line x1={14} y1={0} x2={19} y2={0} className="fits-renderer__reticle-ticks" />
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
                        x={badgeX + 8 + nameLen * 7.2 + 5}
                        y={0}
                        dominantBaseline="central"
                        className="fits-renderer__badge-type"
                      >
                        {specText}
                      </text>
                    )}

                    {/* Navigation Arrow */}
                    <text
                      x={badgeX + badgeWidth - 13}
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
    prevProps.overlayStars === nextProps.overlayStars
  );
});
