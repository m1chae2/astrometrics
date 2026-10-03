import React, { useRef } from 'react';
import { FitsViewerManager } from '../../common/fitsViewer/FitsViewerManager';
import type { FitsRendererHandle } from '../../common/fitsViewer/FitsViewerManager';
import { SectionPanel } from '../../common/components/SectionPanel';
import '../../common/styles/panels.css';

/** Props for the TargetViewerManager component. */
interface Props {
  /** URL of the image to display. */
  imageUrl?: string | null;
  /** Optional Blob of the image data (a stretched FITS file or a picture). */
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
 *
 * The picture is already stretched when it reaches this view, so the viewer
 * is told not to stretch it again. Zoom, pan and the Fit and 1:1 buttons work
 * for a stretched FITS file and for a plain picture alike.
 */
export const TargetViewerManager: React.FC<Props> = ({
  imageUrl,
  imageBlob,
  loading,
  error,
  selectedTarget,
}) => {
  const rendererRef = useRef<FitsRendererHandle | null>(null);

  return (
    <div className="panel-group">
      <SectionPanel
        title="Image Viewer"
        className="flex-fill"
        headerContent={
          <div className="fits-header-controls" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <button className="btn btn--tiny" onClick={() => rendererRef.current?.zoomIn()} title="Zoom In">+</button>
            <button className="btn btn--tiny" onClick={() => rendererRef.current?.zoomOut()} title="Zoom Out">-</button>
            <button className="btn btn--tiny" onClick={() => rendererRef.current?.zoomToFit()} title="Zoom to Fit">Fit</button>
            <button className="btn btn--tiny" onClick={() => rendererRef.current?.zoomToScale(1.0)} title="Actual Size">1:1</button>
          </div>
        }
      >
        <FitsViewerManager
          ref={rendererRef}
          imageUrl={imageUrl}
          imageBlob={imageBlob}
          loading={loading}
          error={error}
          selectedTarget={selectedTarget}
          disableStretch
          stretch={false}
        />
      </SectionPanel>
    </div>
  );
};
