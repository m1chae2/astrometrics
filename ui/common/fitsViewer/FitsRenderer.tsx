import React, { useState, useRef, useEffect, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader } from './hooks/useFitsLoader';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';

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
    disableStretch
  } = props;

  // Local State
  const [status, setStatus] = useState<string | null>(null);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [drawnSize, setDrawnSize] = useState({ w: 0, h: 0 });

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
  useCanvasDrawer(canvasRef, parsedData, bitmap, setDrawnSize, effectiveStretch);

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
    prevProps.stretch === nextProps.stretch
  );
});
