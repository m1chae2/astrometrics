import React, { useCallback, useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { SwitchDisplayButton } from '../../common/components/SwitchDisplayButton';
import { FitsViewerManager, FitsRendererHandle } from '../../common/fitsViewer/FitsViewerManager';
import { FileBrowser, FileBrowserToolbar } from '../fileBrowser/FileBrowser';
import { AstrometryOverlayStar } from '../../common/services/astronomyService';
import { Button } from '../../common/components/Button';
import { DisplayModeSelect } from '../../common/fitsViewer/DisplayModeSelect';
import type { DisplayMode } from '../../common/fitsViewer/displayMode';
import { DetectionsToggle } from '../../common/fitsViewer/DetectionsToggle';
import { resolveTargetFramePath } from '../../common/services/imaging/measurementPath';

/** Shown under an Auto Stretch picture, which holds no pixel values. */

interface ViewerPanelContainerProps {
  // Viewer Props
  fitsRendererRef: React.RefObject<FitsRendererHandle | null>;
  imageUrl: string | null;
  imageBlob: Blob | null;
  imageFrameInfo: any;
  loading: boolean;
  error: string | null;
  selectedTarget: string;
  autoPanTrigger: number;
  disableStretch: boolean;
  /** How the viewer draws the image: Linear or Auto Stretch. */
  displayMode: DisplayMode;
  onDisplayModeChange: (mode: DisplayMode) => void;
  overlayStars?: AstrometryOverlayStar[];
  showAstrometryOverlay?: boolean;
  onToggleAstrometryOverlay?: () => void;
  isLoadingOverlay?: boolean;
  /** Why no overlay markers are drawn (for example, no sky solution), or null. */
  overlayReason?: string | null;
  onStarClick?: (star: AstrometryOverlayStar) => void;
  selectedStarId?: string | null;

  // File Browser Props
  allFiles: any[];
  filteredFiles: any[];
  fileFilterText: string;
  setFileFilterText: (text: string) => void;
  fileBrowserCamera: string;
  setFileBrowserCamera: (camera: string) => void;
  selectedOptic?: string;
  setSelectedOptic?: (optic: string) => void;
  checkedFiles: Set<string>;
  handleRequestDeleteFiles: (paths: string[]) => void;
  onOpenHeaderModal: (path?: string) => void;
  onFileClick: (path: string | null) => void;
  selectedFile: string | null;
  toggleFile: (path: string | null) => void;
  toggleAllFiles: () => void;
  analysisResults: any;

  /** Set while Image Controls is folded and the right column too: its tab joins the bottom bar here. */
  onOpenImageControls?: () => void;
}

/**
 * Component encapsulating the FITS viewer and File Browser sections.
 */
export const ViewerPanelContainer: React.FC<ViewerPanelContainerProps> = ({
  fitsRendererRef, imageUrl, imageBlob, imageFrameInfo, loading, error, selectedTarget,
  autoPanTrigger, disableStretch, displayMode, onDisplayModeChange,
  overlayStars, showAstrometryOverlay, onToggleAstrometryOverlay, isLoadingOverlay, overlayReason, onStarClick,
  selectedStarId,
  allFiles, filteredFiles, fileFilterText, setFileFilterText, fileBrowserCamera, setFileBrowserCamera, selectedOptic, setSelectedOptic,
  checkedFiles, handleRequestDeleteFiles, onOpenHeaderModal, onFileClick, selectedFile,
  toggleFile, toggleAllFiles, analysisResults, onOpenImageControls
}) => {
  // True while the image shows a marker at every star the astrometry run's star finder detects.
  const [detectionsEnabled, setDetectionsEnabled] = useState(false);
  // True while the File Browser is folded down to a tab bar, which gives the viewer the full height.
  const [browserCollapsed, setBrowserCollapsed] = useState(false);

  /** Finds the FITS file on screen: the file picked in the browser, else the light frame being shown. */
  const resolveMeasurePath = useCallback(async (): Promise<string | null> => {
    if (selectedFile) return selectedFile;
    if (imageFrameInfo && imageFrameInfo.iso !== 'Unknown' && imageFrameInfo.objectId) {
      return resolveTargetFramePath(
        imageFrameInfo.objectId, imageFrameInfo.iso, imageFrameInfo.exposure, imageFrameInfo.index ?? 0
      );
    }
    return null;
  }, [selectedFile, imageFrameInfo]);

  return (
    <div className="image-processing-display__center">
      <SectionPanel
        title="FITS Viewer"
        className="image-processing-display__viewer-section"
        headerContent={
          <div className="fits-header-controls">
            <DisplayModeSelect mode={displayMode} onChange={onDisplayModeChange} />
            <DetectionsToggle enabled={detectionsEnabled} onChange={setDetectionsEnabled} />
            <Button
              size="small"
              active={showAstrometryOverlay}
              onClick={onToggleAstrometryOverlay}
              disabled={isLoadingOverlay}
              title={
                isLoadingOverlay
                  ? "Getting astrometry results"
                  : (showAstrometryOverlay ? "Hide Astrometry Overlay" : "Show Astrometry Overlay")
              }
            >
              {isLoadingOverlay ? "Getting stars" : "Astrometry"}
            </Button>
            {showAstrometryOverlay && overlayReason && !isLoadingOverlay && (
              <span
                className="fits-header-controls__note"
                data-testid="astrometry-overlay-reason"
              >
                {overlayReason}
              </span>
            )}
            <Button size="small" onClick={() => fitsRendererRef.current?.zoomIn()} title="Zoom In">+</Button>
            <Button size="small" onClick={() => fitsRendererRef.current?.zoomOut()} title="Zoom Out">-</Button>
            <Button size="small" onClick={() => fitsRendererRef.current?.zoomToFit()} title="Zoom to Fit">Fit</Button>
            <Button size="small" onClick={() => fitsRendererRef.current?.zoomToScale(1.0)} title="Actual Size">1:1</Button>
            <SwitchDisplayButton targetDisplay="Image Viewer" selectedTarget={selectedTarget} />
          </div>
        }
      >
        <FitsViewerManager
          ref={fitsRendererRef}
          imageUrl={imageUrl}
          imageBlob={imageBlob}
          frameInfo={imageFrameInfo}
          loading={loading}
          error={error}
          selectedTarget={selectedTarget}
          autoPanTrigger={autoPanTrigger}
          disableStretch={disableStretch}
          stretch={displayMode === 'auto-stretch'}
          overlayStars={overlayStars}
          showOverlay={showAstrometryOverlay}
          onStarClick={onStarClick}
          selectedStarId={selectedStarId}
          showDetections={detectionsEnabled}
          resolveMeasurePath={resolveMeasurePath}
        />
      </SectionPanel>
      {browserCollapsed ? null : (
      <SectionPanel
        title="File Browser"
        className="image-processing-display__browser-section"
        onTitleClick={() => setBrowserCollapsed(true)}
        titleButtonLabel="Collapse the File Browser to a tab bar"
        headerContent={
          <div className="image-processing-display__browser-header">
            <FileBrowserToolbar
              className="file-browser__toolbar--header"
              files={allFiles}
              searchTerm={fileFilterText}
              onSearchChange={setFileFilterText}
              selectedCamera={fileBrowserCamera}
              onCameraChange={setFileBrowserCamera}
              selectedOptic={selectedOptic}
              onOpticChange={setSelectedOptic}
              checkedFiles={checkedFiles}
              onDeleteFiles={handleRequestDeleteFiles}
              onShowHeader={() => onOpenHeaderModal(selectedFile || undefined)}
            />
          </div>
        }
      >
        <FileBrowser
          files={filteredFiles}
          searchTerm={fileFilterText}
          onSearchChange={setFileFilterText}
          rejectedFiles={analysisResults?.rejectedFiles || []}
          onSelectFile={onFileClick}
          selectedFile={selectedFile}
          onDeleteFiles={handleRequestDeleteFiles}
          checkedFiles={checkedFiles}
          onToggleFile={toggleFile}
          onToggleAll={toggleAllFiles}
          selectedCamera={fileBrowserCamera}
        />
      </SectionPanel>
      )}
      {(browserCollapsed || onOpenImageControls) && (
        <div className="image-processing-display__collapsed-row">
          {browserCollapsed && (
            <SectionPanel
              title="File Browser"
              collapsed="down"
              collapsedNote={`${filteredFiles.length} frames`}
              className="image-processing-display__collapsed-row-fill"
              onTitleClick={() => setBrowserCollapsed(false)}
              titleButtonLabel="Expand the File Browser"
            >
              {null}
            </SectionPanel>
          )}
          {onOpenImageControls && (
            <SectionPanel
              title="Image Controls"
              collapsed="down"
              className="image-processing-display__collapsed-row-end"
              onTitleClick={onOpenImageControls}
              titleButtonLabel="Expand Image Controls"
            >
              {null}
            </SectionPanel>
          )}
        </div>
      )}
    </div>
  );
};
