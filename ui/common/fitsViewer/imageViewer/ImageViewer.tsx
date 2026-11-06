/**
 * @file ImageViewer.tsx
 * @description Shows a processed (non-FITS) picture of a target, or a loading
 * or error message in its place. FitsViewerManager uses it when the image is a
 * normal picture file rather than raw FITS data.
 */
import React, { useLayoutEffect, useState } from 'react';
import { ImageContainer } from './ImageContainer';
import './imageViewer.css';

import { Skeleton } from '../../components/Skeleton';
/** Props for the ImageViewer component. */
interface Props {
  /** URL of the image to display. */
  imageUrl?: string | null;
  /** Optional Blob of the image data. */
  imageBlob?: Blob | null;
  /** Whether the image is currently loading. */
  loading?: boolean;
  /** Error message if image loading failed. */
  error?: string | null;
  /** The currently selected target ID (optional). */
  selectedTarget?: string;
}

/**
 * Component for displaying a target image or status messages (loading, errors).
 */
export const ImageViewer: React.FC<Props> = ({ imageUrl, imageBlob, loading, error, selectedTarget }) => {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  // A layout effect, so that when a paused (hidden) display is shown again the
  // picture gets its new URL before the screen is drawn, not one frame later.
  useLayoutEffect(() => {
    if (imageBlob) {
      const url = URL.createObjectURL(imageBlob);
      setBlobUrl(url);
      return () => URL.revokeObjectURL(url);
    } else {
      setBlobUrl(null);
      return undefined;
    }
  }, [imageBlob]);

  const displayUrl = imageUrl || blobUrl;

  return (
    <div className="image-viewer-frame" id="image-viewer">
      {loading && (
        <div className="image-viewer__loading">
          <Skeleton variant="block" label="Loading the image" />
        </div>
      )}
      {error && !loading && <em className="image-viewer__error">{error}</em>}
      {displayUrl && !error && !loading && (
        <ImageContainer
          src={displayUrl}
          alt="Processed Image"
          className="image-rounded"
        />
      )}
      {!displayUrl && !error && !loading && (
        <span>{selectedTarget ? 'No processed image available for this target.' : 'Select a target to view its image.'}</span>
      )}
    </div>
  );
};
