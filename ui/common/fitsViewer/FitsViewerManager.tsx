import React from 'react';
import { ImageViewer } from './imageViewer/ImageViewer';
import { FitsRenderer } from './FitsRenderer';
import type { FitsRendererHandle } from './FitsRenderer';

import { AstrometryOverlayStar } from '../services/astronomyService';
import { StretchParameters } from '../types/backendTypes';
import type { FocusStar, RestoreView } from './hooks/useViewPlacement';
import type { SavedViewport } from '../utils/imageViewStore';

export type { FitsRendererHandle, FocusStar };

/** Props for the FitsViewerManager component. */
interface Props {
  /** URL of the processed image. */
  imageUrl?: string | null;
  /** Blob of the raw FITS data. */
  imageBlob?: Blob | null;
  /** Metadata for the FITS frame. */
  frameInfo?: {
    objectId: string;
    gain: number | null;
    exposureS: number | null;
    index?: number;
    min?: number;
    max?: number;
  } | null;
  /** Whether data is currently loading. */
  loading?: boolean;
  /** Error message if loading failed. */
  error?: string | null;
  /** The currently selected target ID. */
  selectedTarget?: string;
  /** Trigger to force the renderer to pan/zoom to center. */
  autoPanTrigger?: number;
  /** Whether to disable automatic stretching. */
  disableStretch?: boolean;
  /** Explicit stretch toggle. */
  stretch?: boolean;
  /** Pixel values drawn black and white when `stretch` is false (default: the file's own range). */
  displayRange?: readonly [number, number];
  /** The library's automatic stretch for a FITS image, sent by the backend. */
  stretchParameters?: StretchParameters | null;
  /** Identified star overlay items. */
  overlayStars?: AstrometryOverlayStar[];
  /** Whether astrometry overlay is enabled. */
  showOverlay?: boolean;
  /** Callback when a star in the overlay is clicked. */
  onStarClick?: (star: AstrometryOverlayStar) => void;
  /** Currently selected star identifier for overlay highlighting. */
  selectedStarId?: string | null;
  /** Small stretched picture shown while the full FITS image loads; see `FitsRenderer`. */
  previewBlob?: Blob | null;
  /** Whether this viewer keeps pictures decoded ahead of time. */
  acceptPrefetch?: boolean;
  /** True while the Detections overlay is on; see `FitsRenderer`. */
  showDetections?: boolean;
  /** Finds the file path of the image on screen, for the star profile; see `FitsRenderer`. */
  resolveMeasurePath?: () => Promise<string | null>;
  /** What the star profile panel says when the image holds no pixel values; see `FitsRenderer`. */
  noPixelDataMessage?: string;
  /** A sentence about the picture shown in the star profile panel; see `FitsRenderer`. */
  starProfilePictureNote?: string;
  /** True to show raw frames turned to match the target's stack; see `FitsRenderer`. */
  matchStackTurn?: boolean;
  /** A star to put at the center of the view whenever a picture is drawn; see `FitsRenderer`. */
  focusStar?: FocusStar | null;
  /** A saved view to put back; see `FitsRenderer`. */
  restoreView?: RestoreView | null;
  /** Called when the zoom or the point at the center changes; see `FitsRenderer`. */
  onViewChange?: (view: SavedViewport) => void;
  /** Called with true when the view is at the smallest zoom (the fit); see `FitsRenderer`. */
  onMinimumZoomChange?: (atMinimum: boolean) => void;
}

/**
 * Orchestrator for displaying processed images or raw FITS data.
 * Delegates to FitsRenderer for FITS/raw data and ImageViewer for standard images.
 */
export const FitsViewerManager = React.forwardRef<FitsRendererHandle, Props>(({
  imageUrl,
  imageBlob,
  loading,
  error,
  selectedTarget,
  frameInfo,
  autoPanTrigger,
  disableStretch,
  stretch,
  displayRange,
  stretchParameters,
  overlayStars,
  showOverlay,
  onStarClick,
  selectedStarId,
  previewBlob,
  acceptPrefetch,
  showDetections,
  resolveMeasurePath,
  noPixelDataMessage,
  starProfilePictureNote,
  matchStackTurn,
  focusStar,
  restoreView,
  onViewChange,
  onMinimumZoomChange
}, ref) => {
  // Prefer the client-side FITS renderer if an image URL or blob is provided.
  if (imageUrl || imageBlob || previewBlob) {
    return (
      <FitsRenderer
        ref={ref}
        imageUrl={imageUrl ?? null}
        imageBlob={imageBlob ?? null}
        frameInfo={frameInfo ?? null}
        autoPanTrigger={autoPanTrigger ?? 0}
        loading={loading || false}
        error={error || null}
        selectedTarget={selectedTarget || null}
        disableStretch={disableStretch}
        stretch={stretch}
        displayRange={displayRange}
        stretchParameters={stretchParameters}
        overlayStars={overlayStars}
        showOverlay={showOverlay}
        onStarClick={onStarClick}
        selectedStarId={selectedStarId}
        previewBlob={previewBlob ?? null}
        acceptPrefetch={acceptPrefetch}
        showDetections={showDetections}
        resolveMeasurePath={resolveMeasurePath}
        noPixelDataMessage={noPixelDataMessage}
        starProfilePictureNote={starProfilePictureNote}
        matchStackTurn={matchStackTurn}
        focusStar={focusStar}
        restoreView={restoreView}
        onViewChange={onViewChange}
        onMinimumZoomChange={onMinimumZoomChange}
      />
    );
  }

  // Fallback to the standard image viewer for PNG/JPEG or empty states.
  return (
    <ImageViewer
      imageUrl={imageUrl}
      imageBlob={imageBlob}
      loading={loading}
      error={error}
      selectedTarget={selectedTarget}
    />
  );
});
