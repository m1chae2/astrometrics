import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useTargetActions } from '../common/hooks/useTargetActions';
import { ImageProcessingProvider, useImageProcessingContext } from './context/ImageProcessingContext';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useViewerState } from './hooks/useViewerState';
import { usePersistentFlag } from './hooks/usePersistentFlag';
import { ImageProcessingLayout } from './ImageProcessingLayout';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { ListActions } from '../common/components/ListActions';
import { FitsRendererHandle } from '../common/fitsViewer/FitsViewerManager';
import { ViewerPanelContainer } from './components/ViewerPanelContainer';
import { FrameAnalysisPanel } from './components/FrameAnalysisPanel';
import { addTargetData, createTarget, fetchTargetObject } from '../common/services/targetService';
import { useQuery } from '@tanstack/react-query';
import { fetchAstrometryOverlayStars, AstrometryOverlayStar } from '../common/services/astronomyService';
import { emit as emitEvent } from '../common/utils/eventBus';
import { emitToast } from '../common/utils/emitToast';
import { reportError } from '../common/utils/reportError';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { Button } from '../common/components/Button';
import { useHandoffConsumer } from '../common/utils/displayHandoffStore';

/**
 * Main component for the image processing view.
 * Composes target list, FITS viewer, and processing details.
 */
/**
 * Pick the raw frame with the highest exposure time.
 *
 * @param files - The frames in the file list; `exposure` may read "30s" or "30".
 * @returns The frame to show, or `undefined` for an empty list.
 */
function highestExposureFile<T extends { exposure: string }>(files: T[]): T | undefined {
  let bestFile = files[0];
  let maxExposure = -1;
  files.forEach((file) => {
    const exposureSeconds = parseFloat(file.exposure);
    if (!isNaN(exposureSeconds) && exposureSeconds > maxExposure) {
      maxExposure = exposureSeconds;
      bestFile = file;
    }
  });
  return bestFile;
}

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
  const { remoteTargets } = useRemoteStatusContext();

  const {
    items, isLoading: isTargetListLoading, filterPanel,
    filterText, setFilterText,
    isLocalTarget,
    lightFrames, isLoadingFrames, isProcessing, startProcessing, cancelProcessingJob,
    refreshFrames, isAnalyzing, analysisResults, startAnalysis,
    logLines, clearLogs,
    files
  } = useImageProcessingContext();

  const {
    allFiles, isLoadingFiles, filteredFiles, exposureCounts, fileFilterText, setFileFilterText,
    selectedCamera: fileBrowserCamera, setSelectedCamera: setFileBrowserCamera,
    selectedOptic, setSelectedOptic,
    selectedFile, setSelectedFile, filesToDelete, handleRequestDeleteFiles, confirmDeleteFiles,
    showFileDeleteConfirm, setShowFileDeleteConfirm,
    checkedFiles, setCheckedFiles, toggleFile, toggleAllFiles,
    stackedImage, stackedSpectralTarget, totalExposure,
    starShapeReview, frameStatuses, setAsideFiles, putBackFrame
  } = files;

  const {
    imageUrl, imageBlob, imageFrameInfo, autoPanTrigger, disableStretch,
    loading, error, displayMode, setDisplayMode,
    setSelectedLightRow, selectedLightRow
  } = useViewerState(selectedTarget, lightFrames, selectedFile);

  // "Ready" for the app-boot splash screen means the viewer has actually
  // settled on its initial image, not merely that `loading` happens to be
  // false on the render where this runs -- `loading` starts false and only
  // flips true a couple of effects downstream of the target/frames data
  // arriving (see useViewerState.ts), so checking it alone reports ready
  // before that chain even begins. Gating on `isTargetListLoading` and
  // `isLoadingFrames` (both seeded true-while-something-is-selected, see
  // useStackingJob.ts) closes that gap: this can't go true until the target
  // list and the selected target's frame summary have both genuinely
  // resolved, by which point `loading`'s own true/false transition (if any)
  // is a same-frame formality rather than a multi-second race.
  // A stack that is waiting to be shown counts as something to load, so a target with a stack
  // but no raw frames settles once the stack is on screen.
  const preferredStack = stackedImage ?? stackedSpectralTarget;
  const hasFrameToAutoLoad = (lightFrames.length > 0 || preferredStack !== null) && !selectedFile;
  const isViewerSettled = !loading && (
    !selectedTarget || !hasFrameToAutoLoad || imageUrl !== null || imageBlob !== null || error !== null
  );
  useReportModeReady(
    'Image Processing',
    !isTargetListLoading && !isLoadingFrames && !isLoadingFiles && isViewerSettled
  );

  // The raw frames ticked for stacking must not stay ticked once stacking
  // ends. A leftover selection would be sent to the next Analyze run in
  // place of the stacked result that stacking just produced.
  const wasProcessingRef = React.useRef(false);
  useEffect(() => {
    if (wasProcessingRef.current && !isProcessing) {
      setCheckedFiles(new Set());
    }
    wasProcessingRef.current = isProcessing;
  }, [isProcessing, setCheckedFiles]);

  const fitsRendererRef = React.useRef<FitsRendererHandle>(null);

  // Frame Analysis folds to the right; Image Controls folds down into a tab bar.
  const [analysisCollapsed, setAnalysisCollapsed] = usePersistentFlag('astrometrics:imageProcessing:analysisCollapsed', false);
  const [controlsCollapsed, setControlsCollapsed] = usePersistentFlag('astrometrics:imageProcessing:controlsCollapsed', false);
  const [showAstrometryOverlay, setShowAstrometryOverlay] = useState<boolean>(() => {
    try {
      return localStorage.getItem('astrometrics:enableAstrometryOverlay') === 'true';
    } catch {
      return false;
    }
  });
  const overlayTargetRef = useRef<string | null>(null);

  const [selectedStarId, setSelectedStarId] = useState<string | null>(() => {
    try {
      return localStorage.getItem('astrometrics:imageProcessingSelectedStar');
    } catch {
      return null;
    }
  });

  const pendingEnableAstrometryRef = useRef<boolean>(false);

  // Clear target search filter and pick up incoming star / overlay settings when
  // navigated to a specific target. The request waits in the hand-off store, so
  // it also arrives when this display was hidden or paused when it was sent.
  useHandoffConsumer<string | { targetId?: string; enableAstrometry?: boolean; starId?: string }>(
    'Image Processing',
    'targetSelected',
    (raw) => {
      const targetId = typeof raw === 'string' ? raw : raw?.targetId;
      if (targetId) {
        setFilterText('');
      }
      if (raw && typeof raw === 'object') {
        if (raw.enableAstrometry) {
          pendingEnableAstrometryRef.current = true;
          setShowAstrometryOverlay(true);
        }
        if (raw.starId) {
          setSelectedStarId(raw.starId);
        }
      }
    }
  );

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isHeaderModalOpen, setIsHeaderModalOpen] = useState(false);
  const [headerModalPath, setHeaderModalPath] = useState<string | null>(null);

  const handleOpenHeaderModal = React.useCallback((path?: string) => {
    setHeaderModalPath(path || selectedFile);
    setIsHeaderModalOpen(true);
  }, [selectedFile]);

  const { saveTarget, confirmDeleteTarget, handleCreateTarget } = useTargetActions();

  useEffect(() => {
    // The overlay switch is reset only when the target changes. Showing a
    // different file of the same target keeps it as it is.
    const targetChanged = overlayTargetRef.current !== selectedTarget;
    overlayTargetRef.current = selectedTarget;
    let shouldEnable = pendingEnableAstrometryRef.current;
    try {
      if (localStorage.getItem('astrometrics:enableAstrometryOverlay') === 'true') {
        shouldEnable = true;
        localStorage.removeItem('astrometrics:enableAstrometryOverlay');
      }
    } catch {
      // Ignore
    }
    pendingEnableAstrometryRef.current = false;

    if (shouldEnable) {
      setShowAstrometryOverlay(true);
    } else if (targetChanged) {
      setShowAstrometryOverlay(false);
    }
  }, [selectedTarget, selectedFile]);

  // The overlay stars are only read while the Astrometry switch is on, because
  // nothing shows them otherwise. The key holds the target and the file, so two
  // asks for the same image share one request, and a new file asks again.
  const overlayQuery = useQuery({
    queryKey: ['astrometryOverlayStars', selectedTarget, selectedFile ?? null],
    queryFn: () => fetchAstrometryOverlayStars(selectedTarget as string, 0, selectedFile),
    enabled: Boolean(selectedTarget) && showAstrometryOverlay,
    staleTime: 30_000,
  });
  const overlayStars: AstrometryOverlayStar[] = useMemo(
    () => (showAstrometryOverlay && selectedTarget ? overlayQuery.data?.stars ?? [] : []),
    [showAstrometryOverlay, selectedTarget, overlayQuery.data]
  );
  const overlayReason: string | null = overlayQuery.data?.reason ?? null;
  const isLoadingOverlay = overlayQuery.isFetching && showAstrometryOverlay;

  // Report a failed read, once per failure.
  useEffect(() => {
    if (overlayQuery.error) {
      const error = overlayQuery.error;
      reportError(error instanceof Error ? error : new Error(String(error)), 'backend');
    }
  }, [overlayQuery.error]);

  // When the user turned the switch on and the library has nothing to show and no
  // reason to give, say so and switch it back off.
  const askedForOverlayRef = useRef(false);
  useEffect(() => {
    if (!askedForOverlayRef.current || !overlayQuery.isSuccess || overlayQuery.isFetching) return;
    askedForOverlayRef.current = false;
    const { stars, reason } = overlayQuery.data;
    if (stars.length === 0 && !reason) {
      setShowAstrometryOverlay(false);
      emitToast(
        `No astrometry stars identified for ${selectedTarget}. Run astrometry plate solving first.`,
        'info',
        'Astrometry'
      );
    }
  }, [overlayQuery.isSuccess, overlayQuery.isFetching, overlayQuery.data, selectedTarget]);

  const handleToggleAstrometryOverlay = React.useCallback(() => {
    if (showAstrometryOverlay) {
      setShowAstrometryOverlay(false);
      return;
    }
    if (!selectedTarget) {
      emitToast('No target selected.', 'warning', 'Astrometry');
      return;
    }
    askedForOverlayRef.current = true;
    setShowAstrometryOverlay(true);
  }, [showAstrometryOverlay, selectedTarget, setShowAstrometryOverlay]);

  /**
   * Handles clicking an astrometry star reticle or badge in the FITS viewer.
   * Hands off the selected star and navigates to the Astronomy Display.
   */
  const handleStarClick = React.useCallback((star: AstrometryOverlayStar) => {
    const starId = star.id || star.name;
    if (!starId) return;

    setSelectedStarId(starId);
    try {
      localStorage.setItem('astrometrics:imageProcessingSelectedStar', starId);
      localStorage.setItem('planetariumSelectedStar', starId);
    } catch {
      // Ignore
    }

    navigateToElement({
      targetDisplay: 'Astronomy Manager',
      targetElement: 'spectrumViewer',
      action: 'astronomySelectStar',
      payload: starId,
      toast: {
        message: `Opening ${star.name || starId} in Astronomy Manager`,
        type: 'info',
        title: 'Astrometry',
      },
    }).then((result) => {
      if (!result.handledRemotely) {
        try {
          localStorage.setItem('appMode', 'Astronomy Manager');
        } catch {
          // Ignore
        }
      }
    });
  }, []);

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

  // Remember, for this visit to the target, whether the person chose a file and whether the
  // stack has already been shown. Both start over when the target changes.
  const pickedByUserRef = React.useRef(false);
  const stackShownRef = React.useRef(false);
  const stackFallbackDoneRef = React.useRef(false);
  // Declared before the effect that picks the default file, so it runs first on a target change.
  React.useEffect(() => {
    pickedByUserRef.current = false;
    stackShownRef.current = false;
    stackFallbackDoneRef.current = false;
  }, [selectedTarget]);

  // If the stack the default chose cannot be read, show the raw frame with the highest
  // exposure instead, once, so the viewer does not keep retrying the stack.
  React.useEffect(() => {
    if (!error || stackFallbackDoneRef.current || pickedByUserRef.current) return;
    if (!selectedFile || (selectedFile !== stackedImage && selectedFile !== stackedSpectralTarget)) return;
    stackFallbackDoneRef.current = true;
    const bestFile = highestExposureFile(filteredFiles);
    if (bestFile) setSelectedFile(bestFile.path);
  }, [error, selectedFile, stackedImage, stackedSpectralTarget, filteredFiles, setSelectedFile]);

  // A file chosen by hand (File Browser or stack panel) is never replaced by the default.
  const pickFile = React.useCallback((path: string | null) => {
    pickedByUserRef.current = true;
    setSelectedFile(path);
  }, [setSelectedFile]);

  // --- Action Handlers ---

  // Wrapper for file selection to ensure both UI highlight and Viewer state update
  // Wrapper for file selection to ensure both UI highlight and Viewer state update
  const onFileClick = (path: string | null) => {
    if (!path) return;
    pickedByUserRef.current = true;
    setSelectedFile(path);
    setSelectedLightRow(null);
    // useViewerState now listens to selectedFile from context
  };

  // Choose the file the viewer opens with. The stack is the default: the imaging stack first,
  // then the spectral stack. It is applied once per visit, even if its path arrives after the
  // frame list, and never over a file the person picked. With no stack, the raw frame with
  // the highest exposure is shown.
  React.useEffect(() => {
    if (isLoadingFiles) return;

    if (preferredStack && !pickedByUserRef.current && !stackShownRef.current) {
      stackShownRef.current = true;
      if (selectedFile !== preferredStack) setSelectedFile(preferredStack);
      return;
    }

    // If no files, nothing to do
    if (filteredFiles.length === 0) return;

    // Keep a selection that is in the current list or is a stack
    if (
      selectedFile &&
      (filteredFiles.some(f => f.path === selectedFile) ||
        selectedFile === stackedImage ||
        selectedFile === stackedSpectralTarget)
    ) {
      return;
    }

    // Otherwise, show the raw frame with the highest exposure time
    const bestFile = highestExposureFile(filteredFiles);
    if (bestFile) {
      setSelectedFile(bestFile.path);
    }
  }, [isLoadingFiles, filteredFiles, selectedFile, setSelectedFile, preferredStack, stackedImage, stackedSpectralTarget]);



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
      // Searchable target list with grouping
      items={allItems}
      selectedId={selectedTarget}
      pendingId={pendingTarget}
      onSelect={handleTargetSelect}
      topPanel={<TargetListFilterPanel {...filterPanel} />}
      topPanelTitle="Filter Targets"
      filterText={filterText}
      onFilterTextChange={setFilterText}
      highlightedIds={remoteTargets}
      isLoading={isTargetListLoading}
      loadingMessage="Loading targets"
      emptyMessage="No targets in the catalog."
      // Target management actions enclosed in a consistent panel
      actions={
        <ListActions>
          <Button onClick={saveTarget}>Save Target</Button>
          <Button onClick={() => selectedTarget && setShowDeleteConfirm(true)}>Delete Target</Button>
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
      displayMode={displayMode}
      onDisplayModeChange={setDisplayMode}
      overlayStars={overlayStars}
      showAstrometryOverlay={showAstrometryOverlay}
      onToggleAstrometryOverlay={handleToggleAstrometryOverlay}
      isLoadingOverlay={isLoadingOverlay}
      overlayReason={overlayReason}
      onStarClick={handleStarClick}
      selectedStarId={selectedStarId}
      allFiles={allFiles}
      filteredFiles={filteredFiles}
      fileFilterText={fileFilterText}
      setFileFilterText={setFileFilterText}
      fileBrowserCamera={fileBrowserCamera}
      setFileBrowserCamera={setFileBrowserCamera}
      selectedOptic={selectedOptic}
      setSelectedOptic={setSelectedOptic}
      checkedFiles={checkedFiles}
      handleRequestDeleteFiles={handleRequestDeleteFiles}
      onOpenHeaderModal={handleOpenHeaderModal}
      onFileClick={onFileClick}
      selectedFile={selectedFile}
      toggleFile={toggleFile}
      toggleAllFiles={toggleAllFiles}
      analysisResults={analysisResults}
      frameStatuses={frameStatuses}
      setAsideFiles={setAsideFiles}
      onPutBackFrame={putBackFrame}
      onOpenImageControls={analysisCollapsed && controlsCollapsed ? () => setControlsCollapsed(false) : undefined}
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
      handleSelectFile={pickFile}
      handleAddImages={handleAddImages}
      setIsAddModalOpen={setIsAddModalOpen}
      selectedFile={selectedFile}
      onShowHeader={handleOpenHeaderModal}
      starShapeReview={starShapeReview}
      analysisCollapsed={analysisCollapsed}
      setAnalysisCollapsed={setAnalysisCollapsed}
      controlsCollapsed={controlsCollapsed}
      setControlsCollapsed={setControlsCollapsed}
    />
  );

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
      isHeaderModalOpen={isHeaderModalOpen}
      onCloseHeaderModal={() => setIsHeaderModalOpen(false)}
      targetId={selectedTarget}
      selectedHeaderPath={headerModalPath || selectedFile}
    />
  );
};
