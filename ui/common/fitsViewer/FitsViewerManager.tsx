import React from 'react';
import { ImageViewer } from '../../targetDisplay/targetViewerManager/imageViewer/ImageViewer';
import { FitsRenderer } from './FitsRenderer';
import type { FitsRendererHandle } from './FitsRenderer';

export type { FitsRendererHandle };

/** Props for the FitsViewerManager component. */

/** Props for the FitsViewerManager component. */
interface Props {
  /** URL of the processed image. */
  imageUrl?: string | null;
  /** Blob of the raw FITS data. */
  imageBlob?: Blob | null;
  /** Metadata for the FITS frame. */
  frameInfo?: {
    objectId: string;
    iso: string;
    exposure: string;
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
  stretch
}, ref) => {
  // REQ: IMG-3: Image Visualization
  // REQ: IMG-3.1: The display SHALL render FITS format images directly in the browser.
  // REQ: IMG-3.3: The display SHALL provide pan and zoom capabilities for image inspection.
  // REQ: IMG-3.4: The display SHALL show FITS header metadata (Object object).
  // REQ: IMG-3.5: The display SHALL automatically load and display any new cooling/progress images.
  // Prefer the client-side FITS renderer if an image URL or blob is provided.
  if (imageUrl || imageBlob) {
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
      />
    );
  }

  // Fallback to the standard image viewer for PNG/JPEG or empty states.
  // REQ: IMG-3.2: The display SHALL display standard image formats (JPG, PNG) using a standard viewer.
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
