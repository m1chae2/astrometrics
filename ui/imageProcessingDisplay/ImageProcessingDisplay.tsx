import React, { useState, useMemo } from 'react';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useTargetActions } from '../common/hooks/useTargetActions';
import { ImageProcessingProvider, useImageProcessingContext } from './context/ImageProcessingContext';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useViewerState } from './hooks/useViewerState';
import { ImageProcessingLayout } from './ImageProcessingLayout';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { ListActions } from '../common/components/ListActions';
import { FitsRendererHandle } from '../common/fitsViewer/FitsViewerManager';
import { ViewerPanelContainer } from './components/ViewerPanelContainer';
import { FrameAnalysisPanel } from './components/FrameAnalysisPanel';
import { addTargetData, createTarget, fetchTargetObject } from '../common/services/targetService';
import { emit as emitEvent } from '../common/utils/eventBus';
import { emitToast } from '../common/utils/emitToast';
import { reportError } from '../common/utils/reportError';

/**
 * Main component for the image processing view.
 * Composes target list, FITS viewer, and processing details.
 * REQ: IMG-1: Image Management Interface
 * REQ: IMG-1.1: The display SHALL present a searchable list of images associated with the selected target.
 * REQ: IMG-1.2: The display SHALL show a summary of frame counts per filter/type.
 * REQ: IMG-1.3: The display SHALL allow manual addition of a new target.
 * REQ: IMG-1.4: The display SHALL allow deletion of an existing target.
 * REQ: IMG-1.5: The display SHALL allow manual association of local image files to a target.
 * REQ: IMG-5.1: The display SHALL provide a "Process Target" command to initiate stacking.
 * REQ: IMG-5.2: The display SHALL allow cancellation of an active processing job.
 * REQ: IMG-5.4: The display SHALL indicate the current state of processing.
 */
export const ImageProcessingDisplay: React.FC = () => {
  const {
    selectedTarget, setSelectedTarget,
    pendingTarget, setPendingTarget,
    reloadKey
  } = useTargetContext();

  const { remoteTargets } = useRemoteStatusContext();

  const targetList = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, setSelectedTarget, remoteTargets);

  return (
    <ImageProcessingProvider {...targetList}>
      <ImageProcessingDisplayInner />
    </ImageProcessingProvider>
  );
};

/**
 * Inner component that consumes context.
 */
const ImageProcessingDisplayInner: React.FC = () => {
  const {
    selectedTarget, setSelectedTarget,
    pendingTarget, setPendingTarget,
    reloadKey, forceReload
  } = useTargetContext();

  const {
    items, filterOptions, selectedFilterOption, setFilterOption,
    filterText, setFilterText,
    isLocalTarget,
    lightFrames, isProcessing, startProcessing, cancelProcessingJob,
    refreshFrames, isAnalyzing, analysisResults, startAnalysis,
    logLines, clearLogs,
    files,
    ingestion
  } = useImageProcessingContext();

  const {
    allFiles, filteredFiles, exposureCounts, fileFilterText, setFileFilterText,
    selectedFile, setSelectedFile, filesToDelete, handleRequestDeleteFiles, confirmDeleteFiles,
    showFileDeleteConfirm, setShowFileDeleteConfirm,
    checkedFiles, toggleFile, toggleAllFiles,
    stackedImage, stackedSpectralTarget, totalExposure
  } = files;

  const {
    isIngestModalOpen, openIngestModal, closeIngestModal,
    remoteTargets, setIsIngestModalOpen
  } = ingestion;

  const {
    imageUrl, imageBlob, imageFrameInfo, autoPanTrigger, disableStretch,
    loading, error, stretch, toggleStretch,
    setSelectedLightRow, selectedLightRow
  } = useViewerState(selectedTarget, lightFrames, selectedFile);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isHeaderModalOpen, setIsHeaderModalOpen] = useState(false);
  const [headerModalPath, setHeaderModalPath] = useState<string | null>(null);

  const handleOpenHeaderModal = React.useCallback((path?: string) => {
    setHeaderModalPath(path || selectedFile);
    setIsHeaderModalOpen(true);
  }, [selectedFile]);
  const [fileBrowserCamera, setFileBrowserCamera] = useState('All');

  const { saveTarget, confirmDeleteTarget, handleCreateTarget } = useTargetActions();

  const fitsRendererRef = React.useRef<FitsRendererHandle>(null);

  // Merge remote targets into the list if they don't exist locally
  const allItems = useMemo(() => {
    // Local IDs are already normalized to spaces in useTargetListLogic
    const localIds = new Set((items || []).map(i => i.value));
    const ghosts: Array<{ id: string; value: string; label: string }> = [];

    (remoteTargets || []).forEach(rt => {
      // remoteTargets are already normalized to spaces
      if (!localIds.has(rt)) {
        ghosts.push({
          id: rt,
          value: rt,
          label: rt.replace(/_/g, ' ') // Final safety normalization for label
        });
      }
    });

    return [...(items || []), ...ghosts];
  }, [items, remoteTargets]);

  // --- Action Handlers ---

  // Wrapper for file selection to ensure both UI highlight and Viewer state update
  // Wrapper for file selection to ensure both UI highlight and Viewer state update
  const onFileClick = (path: string | null) => {
    if (!path) return;
    setSelectedFile(path);
    setSelectedLightRow(null);
    // useViewerState now listens to selectedFile from context
  };

  // REQ: Auto-select highest exposure file when list loads
  // When I select a radio button (Target/Filter), the fits viewer should show the first item in the list with the highest exposure time
  React.useEffect(() => {
    // If no files, nothing to do
    if (filteredFiles.length === 0) return;

    // If we already have a valid selection that exists in the current filtered list, keep it
    if (selectedFile && filteredFiles.some(f => f.path === selectedFile)) return;

    // Otherwise, find the file with the highest exposure time
    let bestFile = filteredFiles[0];
    let maxExp = -1;

    (filteredFiles || []).forEach(f => {
      // Parse exposure time safely. Handle "30s", "30", etc.
      const expVal = parseFloat(f.exposure);
      if (!isNaN(expVal)) {
        if (expVal > maxExp) {
          maxExp = expVal;
          bestFile = f;
        }
      }
    });

    if (bestFile) {
      setSelectedFile(bestFile.path);
    }
  }, [filteredFiles, selectedFile, setSelectedFile]);



  const handleAddImages = async (): Promise<void> => {
    if (!selectedTarget) return;
    try {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = '*/*';
      const files: FileList | null = await new Promise((resolve) => {
        input.onchange = () => resolve(input.files);
        input.click();
      });
      if (!files || files.length === 0) return;
      const paths = Array.from(files).map((f: File & { path?: string }) => f.path ?? f.name);
      await addTargetData(paths, selectedTarget);
      refreshFrames();
      emitEvent('targetsUpdated');
    } catch (err) {
      reportError(err, 'addImages');
    }
  };

  const handleDeleteTarget = () => {
    if (selectedTarget) setShowDeleteConfirm(true);
  };

  const confirmDelete = async () => {
    await confirmDeleteTarget();
    setShowDeleteConfirm(false);
  };

  const handleTargetSelect = (id: string) => {
    setPendingTarget(id);
    setSelectedTarget(id);
  };

  // --- Panels ---

  const leftPanel = (
    <RadioListManager
      className="image-processing-display__left"
      title="Targets"
      actionsTitle="Target Controls"
      // REQ: IMG-1.1 - Searchable target list with grouping
      items={allItems}
      selectedId={selectedTarget}
      pendingId={pendingTarget}
      onSelect={handleTargetSelect}
      filterOptions={filterOptions}
      selectedFilterOption={selectedFilterOption}
      onFilterOptionChange={setFilterOption}
      filterText={filterText}
      onFilterTextChange={setFilterText}
      highlightedIds={remoteTargets}
      // REQ: IMG-1.4 - Target management actions enclosed in a consistent panel
      actions={
        <ListActions>
          <button className="btn" onClick={saveTarget}>Save Target</button>
          <button className="btn" onClick={() => selectedTarget && setShowDeleteConfirm(true)}>Delete Target</button>
        </ListActions>
      }
    />
  );

  const centerPanel = (
    <ViewerPanelContainer
      fitsRendererRef={fitsRendererRef}
      imageUrl={imageUrl}
      imageBlob={imageBlob}
      imageFrameInfo={imageFrameInfo}
      loading={loading}
      error={error}
      selectedTarget={selectedTarget}
      autoPanTrigger={autoPanTrigger}
      disableStretch={disableStretch}
      stretch={stretch}
      toggleStretch={toggleStretch}
      allFiles={allFiles}
      filteredFiles={filteredFiles}
      fileFilterText={fileFilterText}
      setFileFilterText={setFileFilterText}
      fileBrowserCamera={fileBrowserCamera}
      setFileBrowserCamera={setFileBrowserCamera}
      checkedFiles={checkedFiles}
      handleRequestDeleteFiles={handleRequestDeleteFiles}
      onOpenHeaderModal={handleOpenHeaderModal}
      onFileClick={onFileClick}
      selectedFile={selectedFile}
      toggleFile={toggleFile}
      toggleAllFiles={toggleAllFiles}
      analysisResults={analysisResults}
    />
  );

  const rightPanel = (
    <FrameAnalysisPanel
      allFiles={allFiles}
      filteredFiles={filteredFiles}
      checkedFiles={checkedFiles}
      stackedImage={stackedImage}
      stackedSpectralTarget={stackedSpectralTarget}
      totalExposure={totalExposure}
      handleSelectFile={setSelectedFile}
      handleAddImages={handleAddImages}
      setIsAddModalOpen={setIsAddModalOpen}
      openIngestModal={openIngestModal}
      remoteTargets={remoteTargets}
      selectedFile={selectedFile}
      onShowHeader={handleOpenHeaderModal}
    />
  );

  const handleIngestComplete = React.useCallback(async (targetName: string) => {
    // Robustness: Ensure target exists before selecting
    const exists = items.some(i => i.value === targetName);
    if (!exists) {
      try {
        const t = await fetchTargetObject(targetName);
        if (!t) {
          await createTarget(targetName);
          emitEvent('terminal:log', `[Ingest] Automatically created target record for ${targetName}`);
        }
      } catch (e) {
        // Attempt create as fallback
        try {
          await createTarget(targetName);
          emitEvent('terminal:log', `[Ingest] Created target record for ${targetName} (fallback)`);
        } catch (err) {
          emitEvent('terminal:log', `[Ingest] Failed to ensure target exists: ${err}`);
        }
      }
    }

    forceReload();
    emitEvent('targetsUpdated');

    setSelectedTarget(targetName);
    setPendingTarget(targetName);
    refreshFrames();
  }, [items, forceReload, setSelectedTarget, setPendingTarget, refreshFrames]);

  return (
    <ImageProcessingLayout
      leftPanel={leftPanel}
      centerPanel={centerPanel}
      rightPanel={rightPanel}
      isAddModalOpen={isAddModalOpen}
      onCloseAddModal={() => setIsAddModalOpen(false)}
      onAddTargetSubmit={handleCreateTarget}
      showDeleteConfirm={showDeleteConfirm}
      targetToDelete={selectedTarget}
      onConfirmDelete={confirmDelete}
      onCancelDelete={() => setShowDeleteConfirm(false)}
      showFileDeleteConfirm={showFileDeleteConfirm}
      fileDeleteCount={filesToDelete.length}
      onConfirmFileDelete={confirmDeleteFiles}
      onCancelFileDelete={() => setShowFileDeleteConfirm(false)}
      isIngestModalOpen={isIngestModalOpen}
      onCloseIngestModal={closeIngestModal}
      onIngestComplete={handleIngestComplete}
      ingestionState={ingestion}
      isHeaderModalOpen={isHeaderModalOpen}
      onCloseHeaderModal={() => setIsHeaderModalOpen(false)}
      targetId={selectedTarget}
      selectedHeaderPath={headerModalPath || selectedFile}
    />
  );
};
