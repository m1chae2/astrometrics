import React from 'react';
import { ImageViewer } from './imageViewer/ImageViewer';
import { SectionPanel } from '../../common/components/SectionPanel';
import '../../common/styles/panels.css';

/** Props for the TargetViewerManager component. */
interface Props {
  /** URL of the image to display. */
  imageUrl?: string | null;
  /** Optional Blob of the image data. */
  imageBlob?: Blob | null;
  /** Whether the image is currently loading. */
  loading?: boolean;
  /** Error message if image loading failed. */
  error?: string | null;
  /** The target ID currently selected. */
  selectedTarget?: string;
}

/**
 * Orchestrates the viewing of target images.
 */
export const TargetViewerManager: React.FC<Props> = ({
  imageUrl,
  imageBlob,
  loading,
  error,
  selectedTarget,
}) => {
  return (
    <div className="panel-group">
      <SectionPanel title="Image Viewer" className="flex-fill">
        <ImageViewer
          imageUrl={imageUrl}
          imageBlob={imageBlob}
          loading={loading}
          error={error}
          selectedTarget={selectedTarget}
        />
      </SectionPanel>
    </div>
  );
};
