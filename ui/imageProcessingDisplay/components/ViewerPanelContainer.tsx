import React from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { FitsViewerManager, FitsRendererHandle } from '../../common/fitsViewer/FitsViewerManager';
import { FileBrowser, FileBrowserToolbar } from '../fileBrowser/FileBrowser';

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
  allFiles, filteredFiles, fileFilterText, setFileFilterText, fileBrowserCamera, setFileBrowserCamera,
  checkedFiles, handleRequestDeleteFiles, onOpenHeaderModal, onFileClick, selectedFile,
  toggleFile, toggleAllFiles, analysisResults
}) => {
  const stackedFwhm = (analysisResults as any)?.stackedFwhmPx ?? (analysisResults as any)?.stacked_fwhm_px;
  const rejectedCount = analysisResults?.rejectedCount ?? 0;

  return (
    <div className="image-processing-display__center">
      <SectionPanel
        title="FITS Viewer"
        className="image-processing-display__viewer-section"
        headerContent={
          <div className="fits-header-controls" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            {stackedFwhm !== undefined && stackedFwhm !== null && (
              <span
                style={{
                  fontSize: '0.75rem',
                  padding: '2px 8px',
                  borderRadius: '10px',
                  background: 'rgba(0, 229, 255, 0.12)',
                  border: '1px solid rgba(0, 229, 255, 0.4)',
                  color: '#00e5ff',
                  fontWeight: 600,
                  marginRight: '6px'
                }}
                title="Stack Quality: FWHM & Rejected frames count"
              >
                FWHM {Number(stackedFwhm).toFixed(2)}px • {rejectedCount} Rejected
              </span>
            )}
            <button
              className={`btn btn--tiny ${stretch ? 'btn--active' : ''}`}
              onClick={toggleStretch}
              title={stretch ? "Switch to Linear view" : "Switch to Auto-Stretched view"}
            >
              {stretch ? "Stretch" : "Linear"}
            </button>
            <button className="btn btn--tiny" onClick={() => fitsRendererRef.current?.zoomIn()} title="Zoom In">+</button>
            <button className="btn btn--tiny" onClick={() => fitsRendererRef.current?.zoomOut()} title="Zoom Out">-</button>
            <button className="btn btn--tiny" onClick={() => fitsRendererRef.current?.zoomToFit()} title="Zoom to Fit">Fit</button>
            <button className="btn btn--tiny" onClick={() => fitsRendererRef.current?.zoomToScale(1.0)} title="Actual Size">1:1</button>
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
