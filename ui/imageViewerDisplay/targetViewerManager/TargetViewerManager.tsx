import React, { useCallback, useRef, useState } from 'react';
import { FitsViewerManager } from '../../common/fitsViewer/FitsViewerManager';
import type { FitsRendererHandle } from '../../common/fitsViewer/FitsViewerManager';
import { SectionPanel } from '../../common/components/SectionPanel';
import { SwitchDisplayButton } from '../../common/components/SwitchDisplayButton';
import '../../common/styles/panels.css';
import { Button } from '../../common/components/Button';
import { Tabs } from '../../common/components/Tabs';
import { DetectionsToggle } from '../../common/fitsViewer/DetectionsToggle';
import { resolveProcessedImagePath } from '../../common/services/imaging/measurementPath';

/** Said in the star profile panel: the picture is already stretched, so its brightness is not in camera counts. */
const STRETCHED_PICTURE_NOTE = 'This picture is already stretched, so its brightness values are not raw counts. The widths are still good.';

/** Said in the star profile panel when the target's picture is a plain image with no pixel values. */
const NO_PIXEL_DATA_MESSAGE = 'This picture is not a FITS file, so its stars cannot be measured here.';

/**
 * The pixel values of a stretched picture run from 0 (black) to 1 (white).
 * Drawing it on this fixed scale keeps the sky as dark as the stacking
 * pipeline made it, instead of rescaling to the picture's own extremes.
 * Declared once so the viewer is not redrawn on every render.
 */
const STRETCHED_PICTURE_RANGE = [0, 1] as const;

/** Which picture of a target is shown: the black-and-white stack or its color picture. */
export type PictureChoice = 'mono' | 'color';

/** The two choices of the picture switch. */
const PICTURE_CHOICES = [
  { id: 'mono', label: 'Black and white' },
  { id: 'color', label: 'Color' },
];

/** Props for the TargetViewerManager component. */
interface Props {
  /** URL of the image to display. */
  imageUrl?: string | null;
  /** Optional Blob of the image data (a stretched FITS file or a picture). */
  imageBlob?: Blob | null;
  /** Small preview picture shown while the full FITS image loads. */
  previewBlob?: Blob | null;
  /** Whether the image is currently loading. */
  loading?: boolean;
  /** Error message if image loading failed. */
  error?: string | null;
  /** The target ID currently selected. */
  selectedTarget?: string;
  /**
   * The picture chosen with the black-and-white or color switch. Leave it out when the
   * target has no color picture; the switch is then hidden.
   */
  pictureChoice?: PictureChoice;
  /** Called with the picture the user chose. */
  onPictureChoiceChange?: (choice: PictureChoice) => void;
}

/**
 * Orchestrates the viewing of target images.
 *
 * The picture is already stretched when it reaches this view, so the viewer
 * is told not to stretch it again and to draw it on its fixed 0 to 1 scale.
 * Zoom, pan and the Fit and 1:1 buttons work for a stretched FITS file and
 * for a plain picture alike.
 */
export const TargetViewerManager: React.FC<Props> = ({
  imageUrl,
  imageBlob,
  previewBlob,
  loading,
  error,
  selectedTarget,
  pictureChoice,
  onPictureChoiceChange,
}) => {
  const rendererRef = useRef<FitsRendererHandle | null>(null);
  // True while the picture shows a marker at every star the astrometry run's star finder detects.
  const [detectionsEnabled, setDetectionsEnabled] = useState(false);
  // True while the picture is at its smallest zoom (the fit), so the zoom-out button is off.
  const [viewAtMinimumZoom, setViewAtMinimumZoom] = useState(false);

  /** Finds the FITS file of the target's picture, so the backend measures the file on screen. */
  const resolveMeasurePath = useCallback(
    async (): Promise<string | null> => (selectedTarget ? resolveProcessedImagePath(selectedTarget) : null),
    [selectedTarget]
  );

  return (
    <div className="panel-group">
      <SectionPanel
        title="Image Viewer"
        className="flex-fill"
        headerContent={
          <div className="fits-header-controls" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            {pictureChoice && onPictureChoiceChange && (
              <Tabs
                size="compact"
                idPrefix="image-viewer-picture"
                ariaLabel="Picture colors"
                tabs={PICTURE_CHOICES}
                selectedId={pictureChoice}
                onChange={(choice) => onPictureChoiceChange(choice as PictureChoice)}
              />
            )}
            <DetectionsToggle enabled={detectionsEnabled} onChange={setDetectionsEnabled} />
            <Button size="small" onClick={() => rendererRef.current?.zoomIn()} title="Zoom In">+</Button>
            <Button size="small" disabled={viewAtMinimumZoom} onClick={() => rendererRef.current?.zoomOut()} title="Zoom Out">-</Button>
            <Button size="small" onClick={() => rendererRef.current?.zoomToFit()} title="Zoom to Fit">Fit</Button>
            <Button size="small" onClick={() => rendererRef.current?.zoomToScale(1.0)} title="Actual Size">1:1</Button>
            <SwitchDisplayButton targetDisplay="Image Processing" selectedTarget={selectedTarget} />
          </div>
        }
      >
        <FitsViewerManager
          ref={rendererRef}
          onMinimumZoomChange={setViewAtMinimumZoom}
          imageUrl={imageUrl}
          imageBlob={imageBlob}
          previewBlob={previewBlob}
          acceptPrefetch
          loading={loading}
          error={error}
          selectedTarget={selectedTarget}
          disableStretch
          stretch={false}
          displayRange={STRETCHED_PICTURE_RANGE}
          showDetections={detectionsEnabled}
          resolveMeasurePath={resolveMeasurePath}
          noPixelDataMessage={NO_PIXEL_DATA_MESSAGE}
          starProfilePictureNote={STRETCHED_PICTURE_NOTE}
        />
      </SectionPanel>
    </div>
  );
};
