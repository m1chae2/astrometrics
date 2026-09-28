import React, { useEffect, useState } from 'react';
import { ImageContainer } from '../ImageContainer';
import './imageViewer.css';

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

  useEffect(() => {
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
          <div className="loading-spinner" style={{ width: 40, height: 40, borderWidth: 4 }} />
          <p>Loading...</p>
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
