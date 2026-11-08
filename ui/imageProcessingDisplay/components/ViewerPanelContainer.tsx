import React from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { SwitchDisplayButton } from '../../common/components/SwitchDisplayButton';
import { FitsViewerManager, FitsRendererHandle } from '../../common/fitsViewer/FitsViewerManager';
import { FileBrowser, FileBrowserToolbar } from '../fileBrowser/FileBrowser';
import { AstrometryOverlayStar } from '../../common/services/astronomyService';
import { Button } from '../../common/components/Button';

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
  stretch: boolean;
  toggleStretch: () => void;
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
  checkedFiles: Set<string>;
  handleRequestDeleteFiles: (paths: string[]) => void;
  onOpenHeaderModal: (path?: string) => void;
  onFileClick: (path: string | null) => void;
  selectedFile: string | null;
  toggleFile: (path: string | null) => void;
  toggleAllFiles: () => void;
  analysisResults: any;
}

/**
 * Component encapsulating the FITS viewer and File Browser sections.
 */
export const ViewerPanelContainer: React.FC<ViewerPanelContainerProps> = ({
  fitsRendererRef, imageUrl, imageBlob, imageFrameInfo, loading, error, selectedTarget,
  autoPanTrigger, disableStretch, stretch, toggleStretch,
  overlayStars, showAstrometryOverlay, onToggleAstrometryOverlay, isLoadingOverlay, overlayReason, onStarClick,
  selectedStarId,
  allFiles, filteredFiles, fileFilterText, setFileFilterText, fileBrowserCamera, setFileBrowserCamera,
  checkedFiles, handleRequestDeleteFiles, onOpenHeaderModal, onFileClick, selectedFile,
  toggleFile, toggleAllFiles, analysisResults
}) => {
  return (
    <div className="image-processing-display__center">
      <SectionPanel
        title="FITS Viewer"
        className="image-processing-display__viewer-section"
        headerContent={
          <div className="fits-header-controls">
            <Button
              size="small"
              active={stretch}
              onClick={toggleStretch}
              title={stretch ? "Switch to Linear view" : "Switch to Auto-Stretched view (a stack shows its Siril-stretched JPEG when one exists)"}
            >
              {stretch ? "Stretch" : "Linear"}
            </Button>
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
          stretch={stretch}
          overlayStars={overlayStars}
          showOverlay={showAstrometryOverlay}
          onStarClick={onStarClick}
          selectedStarId={selectedStarId}
        />
      </SectionPanel>
      <SectionPanel
        title="File Browser"
        className="image-processing-display__browser-section"
        headerContent={
          <FileBrowserToolbar
            className="file-browser__toolbar--header"
            files={allFiles}
            searchTerm={fileFilterText}
            onSearchChange={setFileFilterText}
            selectedCamera={fileBrowserCamera}
            onCameraChange={setFileBrowserCamera}
            checkedFiles={checkedFiles}
            onDeleteFiles={handleRequestDeleteFiles}
            onShowHeader={() => onOpenHeaderModal(selectedFile || undefined)}
          />
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
    </div>
  );
};
