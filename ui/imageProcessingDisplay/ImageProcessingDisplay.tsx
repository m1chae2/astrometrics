import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useTargetActions } from '../common/hooks/useTargetActions';
import { ImageProcessingProvider, useImageProcessingContext } from './context/ImageProcessingContext';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useViewerState } from './hooks/useViewerState';
import { useBlinkPair, useBlinkPreload } from './hooks/useBlinkPair';
import { usePersistentFlag } from '../common/hooks/usePersistentFlag';
import { ImageProcessingLayout } from './ImageProcessingLayout';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { ListActions } from '../common/components/ListActions';
import { FitsRendererHandle } from '../common/fitsViewer/FitsViewerManager';
import { ViewerPanelContainer } from './components/ViewerPanelContainer';
import { useStackOrientation } from './hooks/useStackOrientation';
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
import { peekHandoffs, useHandoffConsumer } from '../common/utils/displayHandoffStore';
import {
  loadImageView, markImageViewReturned, RETURN_TO_IMAGE_ACTION, saveImageView,
} from '../common/utils/imageViewStore';
import type { SavedImageView, SavedViewport, StarFocusHandoff } from '../common/utils/imageViewStore';
import type { RestoreView } from '../common/fitsViewer/FitsRenderer';
import { isStarSelected } from '../common/fitsViewer/FitsRenderer';
import { publishedStarFromOverlay, useSelectionPublisher } from '../common/hooks/useSelectionPublisher';
import type { DisplayMode } from '../common/fitsViewer/displayMode';
import { useStarFocusRequest, type StarFocusRequest } from './hooks/useStarFocusRequest';
import { usePhotometryStars } from './hooks/usePhotometryStars';
import { useStackIdentification } from './hooks/useStackIdentification';
import { isStackChoicePath, stackFilterMatch } from './utils/stackChoices';

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

/** How long, in milliseconds, the view must rest before it is written to storage. */
const SAVE_VIEW_DELAY_MS = 300;

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
    stackedImage, stackedSpectralTarget, stackConfiguration,
    stackChoices, viewedStack, viewedStackKey, setChosenStackKey,
    starShapeReview, frameStatuses, frameReport, setAsideFiles, putBackFrame
  } = files;

  // Blink: a flagged or set-aside frame can be compared with a typical frame of its session. The viewer
  // shows the picked frame, or the typical frame while Blink is on.
  const blink = useBlinkPair(selectedTarget, selectedFile, selectedFile ? frameStatuses.get(selectedFile) : undefined);
  const {
    imageUrl, imageBlob, imageFrameInfo, autoPanTrigger, disableStretch,
    loading, error, displayMode, setDisplayMode,
    setSelectedLightRow, selectedLightRow, loadedPath
  } = useViewerState(selectedTarget, lightFrames, blink.shownPath);
  useBlinkPreload(blink.framePaths, displayMode === 'auto-stretch');


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
  // Raw frames are shown turned to match the stack, so the two sides of a meridian flip look alike.
  const stackOrientation = useStackOrientation(selectedTarget, stackedImage ?? null);
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

  // Frame Analysis folds the whole right column to a rail; Image Controls also folds down on its own.
  const [analysisCollapsed, setAnalysisCollapsed] = usePersistentFlag('astrometrics:imageProcessing:analysisCollapsed', false);
  const [controlsCollapsed, setControlsCollapsed] = usePersistentFlag('astrometrics:imageProcessing:controlsCollapsed', false);
  // The whole left column (filters, target list, target controls) folds to a thin strip.
  const [targetListCollapsed, setTargetListCollapsed] = usePersistentFlag('astrometrics:imageProcessing:targetListCollapsed', false);
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
  // Set below, once the pieces it needs exist: shows one star asked for by the Astronomy Manager's "Back to image".
  const starFocusRequestRef = useRef<(request: StarFocusRequest) => void>(() => undefined);
  // Set below too: shows one file asked for by the Command Console's show(target=..., path=...), once the target's files load.
  const fileRequestRef = useRef<(targetId: string, filePath: string) => void>(() => undefined);
  useHandoffConsumer<string | { targetId?: string; enableAstrometry?: boolean; starId?: string; filePath?: string } & Partial<StarFocusHandoff>>(
    'Image Processing',
    'targetSelected',
    (raw) => {
      // Opening a target on purpose wins over the view saved when the screen was left.
      markImageViewReturned();
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
        if (targetId && raw.focusLibraryStarId) {
          starFocusRequestRef.current({ targetId, recordId: raw.focusLibraryStarId, starName: raw.focusStarName ?? '' });
        } else if (targetId && raw.filePath) {
          fileRequestRef.current(targetId, raw.filePath);
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

  // On the imaging stack, the Astrometry button shows the stack's own star names: every detection,
  // named from Gaia and SIMBAD, and the catalog stars it missed (made by a job on first use). On a
  // raw frame it shows the target's catalog stars, placed only when the frame has a real solution.
  // Any stack by camera and optic counts: its star names are kept by its own path.
  const isStackShown = Boolean(
    selectedFile && ((stackedImage && selectedFile === stackedImage) || isStackChoicePath(stackChoices, selectedFile))
  );
  const stackIdentification = useStackIdentification(
    selectedTarget, isStackShown ? selectedFile : null, showAstrometryOverlay && isStackShown
  );

  // The overlay stars are only read while the Astrometry switch is on, because
  // nothing shows them otherwise. The key holds the target and the file, so two
  // asks for the same image share one request, and a new file asks again.
  const overlayQuery = useQuery({
    queryKey: ['astrometryOverlayStars', selectedTarget, selectedFile ?? null],
    queryFn: () => fetchAstrometryOverlayStars(selectedTarget as string, 0, selectedFile),
    enabled: Boolean(selectedTarget) && showAstrometryOverlay && !isStackShown,
    staleTime: 30_000,
  });
  const namedOverlayStars: AstrometryOverlayStar[] = useMemo(
    () => {
      if (!showAstrometryOverlay || !selectedTarget) return [];
      if (isStackShown) return stackIdentification.stars;
      return overlayQuery.data?.stars ?? [];
    },
    [showAstrometryOverlay, selectedTarget, isStackShown, stackIdentification.stars, overlayQuery.data]
  );
  // On a stack, only the named stars with photometry are drawn, so each one opens in the Astronomy
  // Manager with a light curve. One call reads the target's stars with photometry.
  const photometryStars = usePhotometryStars(
    isStackShown ? namedOverlayStars : [], selectedTarget, showAstrometryOverlay && isStackShown
  );
  // A raw frame's overlay stars are read from the library, so each one's id is its record id.
  const libraryStars: AstrometryOverlayStar[] = useMemo(
    () => (isStackShown ? [] : namedOverlayStars.map((star) => ({ ...star, libraryId: star.id }))),
    [isStackShown, namedOverlayStars]
  );
  const overlayStars = isStackShown ? photometryStars.stars : libraryStars;
  const overlayReason: string | null = isStackShown
    ? ([photometryStars.countText, stackIdentification.statusText].filter(Boolean).join('. ') || null)
    : overlayQuery.data?.reason ?? null;
  // A stack shows the loading state while its names are read or made, so a switched-on button with
  // nothing drawn yet says it is working.
  const isLoadingOverlay = showAstrometryOverlay && (isStackShown
    ? stackIdentification.running || !stackIdentification.isLoaded || photometryStars.isLoading
    : overlayQuery.isFetching);

  // The first click on a stack without saved names starts the job that makes them.
  const wantStackNamesRef = useRef(false);
  const { isLoaded: stackNamesLoaded, hasResult: stackHasNames, running: stackNaming, start: startStackNames } = stackIdentification;
  useEffect(() => {
    if (!wantStackNamesRef.current || !isStackShown || !showAstrometryOverlay || !stackNamesLoaded) return;
    wantStackNamesRef.current = false;
    if (!stackHasNames && !stackNaming) void startStackNames();
  }, [isStackShown, showAstrometryOverlay, stackNamesLoaded, stackHasNames, stackNaming, startStackNames]);

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
    if (isStackShown) {
      wantStackNamesRef.current = true;
    } else {
      askedForOverlayRef.current = true;
    }
    setShowAstrometryOverlay(true);
  }, [showAstrometryOverlay, selectedTarget, isStackShown, setShowAstrometryOverlay]);

  // --- The saved view: leaving this screen and coming back lands on the same spot ---
  // The Detections switch lives here, so it can be saved with the rest of the view.
  const [detectionsEnabled, setDetectionsEnabled] = useState(false);
  // Where the view of the picture is now, as reported by the viewer.
  const latestViewRef = useRef<SavedViewport | null>(null);
  // What would be saved if the screen were left now. It is rewritten on every render.
  const snapshotRef = useRef<Omit<SavedImageView, 'view' | 'returnPending' | 'arrivedFromStar'>>({
    target: null, file: null, displayMode: 'auto-stretch', astrometry: false, detections: false,
  });
  useEffect(() => {
    snapshotRef.current = {
      target: selectedTarget || null,
      file: selectedFile,
      displayMode,
      astrometry: showAstrometryOverlay,
      detections: detectionsEnabled,
    };
  });
  const saveTimerRef = useRef<number | null>(null);
  /**
   * Writes the saved view now. Nothing is written before a target and a file are chosen, so a screen that has
   * not settled yet cannot replace a good record.
   *
   * @param options `returnPending` is true when the screen is being left; `arrivedFromStar` names the star that
   *   was opened from the picture (left out, the star already recorded is kept while the screen is being left).
   */
  const persistView = React.useCallback((options: { returnPending: boolean; arrivedFromStar?: string | null; arrivedFromRecord?: string | null }) => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    const snapshot = snapshotRef.current;
    if (!snapshot.target || !snapshot.file) return;
    const arrivedFromStar = options.arrivedFromStar !== undefined
      ? options.arrivedFromStar
      : options.returnPending ? loadImageView()?.arrivedFromStar ?? null : null;
    const arrivedFromRecord = options.arrivedFromRecord !== undefined
      ? options.arrivedFromRecord
      : options.returnPending ? loadImageView()?.arrivedFromRecord ?? null : null;
    saveImageView({ ...snapshot, view: latestViewRef.current, returnPending: options.returnPending, arrivedFromStar, arrivedFromRecord });
  }, []);
  // The viewer reports each change of zoom or center; the record is written once the view rests.
  const handleViewChange = React.useCallback((view: SavedViewport) => {
    latestViewRef.current = view;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => persistView({ returnPending: false }), SAVE_VIEW_DELAY_MS);
  }, [persistView]);

  // A view to put back, waiting for the target and its files; then the request handed to the viewer.
  const [pendingRestore, setPendingRestore] = useState<SavedImageView | null>(null);
  const [restoreView, setRestoreView] = useState<RestoreView | null>(null);
  const restoreCountRef = useRef(0);
  const selectedTargetRef = useRef(selectedTarget);
  const setSelectedTargetRef = useRef(setSelectedTarget);
  useEffect(() => {
    selectedTargetRef.current = selectedTarget;
    setSelectedTargetRef.current = setSelectedTarget;
  });
  const beginRestore = React.useCallback((saved: SavedImageView) => {
    markImageViewReturned();
    if (saved.target && saved.target !== selectedTargetRef.current) setSelectedTargetRef.current(saved.target);
    setPendingRestore(saved);
  }, []);

  // When this screen is shown (on first mount, or again after another screen), put back the view that was
  // saved when it was left. When it is hidden, save the view. An explicit request to open a target wins over the saved view.
  useEffect(() => {
    const saved = loadImageView();
    if (saved?.returnPending) {
      if (peekHandoffs('Image Processing', 'targetSelected').length > 0) markImageViewReturned();
      else beginRestore(saved);
    }
    return () => persistView({ returnPending: true });
  }, [beginRestore, persistView]);

  // The "Back to image" button of the Astronomy Manager asks for the view too; this also works when this
  // screen is already showing (in another window, for example).
  useHandoffConsumer<null>('Image Processing', RETURN_TO_IMAGE_ACTION, () => {
    const saved = loadImageView();
    if (saved) beginRestore(saved);
  });

  /**
   * Handles clicking an astrometry star reticle or badge in the FITS viewer.
   * Hands off the selected star and navigates to the Astronomy Display.
   */
  const handleStarClick = React.useCallback((star: AstrometryOverlayStar) => {
    // The star's own id keeps it selected on the image; the library knows it by its catalog key.
    const selectionKey = star.id || star.name;
    if (!selectionKey) return;
    const recordId = star.libraryId;
    if (!recordId) return;

    setSelectedStarId(selectionKey);
    // Remember the view, so the "Back to image" button (or any way back) lands on the same spot.
    persistView({ returnPending: true, arrivedFromStar: selectionKey, arrivedFromRecord: recordId });
    try {
      localStorage.setItem('astrometrics:imageProcessingSelectedStar', selectionKey);
      localStorage.setItem('planetariumSelectedStar', recordId);
    } catch {
      // Ignore
    }

    navigateToElement({
      targetDisplay: 'Astronomy Manager',
      targetElement: 'spectrumViewer',
      action: 'astronomySelectStar',
      payload: { starId: recordId, targetId: selectedTarget },
      toast: {
        message: `Opening ${star.name || recordId} in Astronomy Manager`,
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
  }, [persistView, selectedTarget]);

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

  // Show the stack of another camera or lens (or the main one again). The main stack does not change.
  // Picking a stack also sets the File Browser's camera and optic filters to that stack's, once, at
  // the moment of the pick. A filter with no matching choice is left alone. The person can change
  // the filters again afterwards, and changing them never changes the picked stack.
  const handleChooseStack = React.useCallback((configurationKey: string) => {
    const choice = stackChoices.find((item) => item.configurationKey === configurationKey);
    if (!choice) return;
    setChosenStackKey(choice.isMain ? null : choice.configurationKey);
    const filters = stackFilterMatch(choice, allFiles);
    // A new camera puts the optic back to its default, so the optic is set after the camera.
    if (filters.camera !== null && filters.camera !== fileBrowserCamera) setFileBrowserCamera(filters.camera);
    if (filters.optic !== null) setSelectedOptic(filters.optic);
    pickFile(choice.stackedImage);
  }, [stackChoices, setChosenStackKey, allFiles, fileBrowserCamera, setFileBrowserCamera, setSelectedOptic, pickFile]);

  // "Back to image" from the Astronomy Manager for a star (maybe of another target): show the target's main
  // stack, select the star and zoom in on it. This wins over the view saved when the screen was left.
  const starFocus = useStarFocusRequest({
    selectedTarget,
    isLoadingFiles,
    mainStackPath: stackedImage ?? null,
    isStackShown,
    // A file of this target, not one left over from the target shown before.
    hasChosenFile: Boolean(selectedFile) && (
      selectedFile === stackedImage || selectedFile === stackedSpectralTarget
      || isStackChoicePath(stackChoices, selectedFile) || allFiles.some((file) => file.path === selectedFile)
    ),
    showMainStack: () => {
      const main = stackChoices.find((choice) => choice.isMain);
      if (main) {
        handleChooseStack(main.configurationKey);
        return;
      }
      stackShownRef.current = true;
      setChosenStackKey(null);
      pickFile(stackedImage ?? null);
    },
    identification: stackIdentification,
    onFound: (star, view) => {
      const selectionKey = star.id || star.name;
      setSelectedStarId(selectionKey);
      try {
        localStorage.setItem('astrometrics:imageProcessingSelectedStar', selectionKey);
      } catch {
        // The selection still shows; it is only not remembered.
      }
      if (view) setRestoreView(view);
    },
  });
  // A star the stack does not name is a short status, not an error.
  useEffect(() => {
    if (starFocus.note) emitToast(starFocus.note, 'info', 'Astrometry');
  }, [starFocus.note]);
  // A layout effect runs before every plain effect, so a request waiting at mount reaches this function.
  const requestStarFocus = starFocus.request;
  React.useLayoutEffect(() => {
    starFocusRequestRef.current = (request) => {
      setPendingRestore(null);
      requestStarFocus(request);
    };
  }, [requestStarFocus]);

  // The Command Console's show(target=..., path=...) asks for one file. It is put back the same way as a saved
  // view (once the target's files have loaded), keeping the display mode and overlays as they are now.
  React.useLayoutEffect(() => {
    fileRequestRef.current = (targetId, filePath) => {
      setPendingRestore({
        ...snapshotRef.current, target: targetId, file: filePath, view: null, returnPending: false, arrivedFromStar: null,
      });
    };
  }, []);

  // --- What is picked, for the Command Console's `selection` object ---
  // The selected star is sent only while it is one of the overlay's stars on the shown picture, so a star
  // remembered from another file or target is never sent. The drawn stars come first, since they carry the library id.
  const selectedOverlayStar = useMemo(() => {
    if (!selectedStarId) return null;
    return overlayStars.find((star) => isStarSelected(star, selectedStarId))
      ?? namedOverlayStars.find((star) => isStarSelected(star, selectedStarId))
      ?? null;
  }, [selectedStarId, overlayStars, namedOverlayStars]);
  const isSpectralStackShown = Boolean(selectedFile && stackedSpectralTarget && selectedFile === stackedSpectralTarget);
  useSelectionPublisher({
    target: selectedTarget || null,
    path: selectedFile || null,
    kind: selectedFile ? (isStackShown || isSpectralStackShown ? 'stack' : 'frame') : null,
    star: publishedStarFromOverlay(selectedOverlayStar),
  });

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

    // Keep a selection that is in the current list, is any frame of the target (the timeline shows every camera and the set-aside frames) or is a stack
    if (
      selectedFile &&
      (filteredFiles.some(f => f.path === selectedFile) ||
        setAsideFiles.some(f => f.path === selectedFile) ||
        allFiles.some(f => f.path === selectedFile) ||
        selectedFile === stackedImage ||
        selectedFile === stackedSpectralTarget ||
        isStackChoicePath(stackChoices, selectedFile))
    ) {
      return;
    }

    // Otherwise, show the raw frame with the highest exposure time
    const bestFile = highestExposureFile(filteredFiles);
    if (bestFile) {
      setSelectedFile(bestFile.path);
    }
  }, [isLoadingFiles, filteredFiles, selectedFile, setSelectedFile, preferredStack, stackedImage, stackedSpectralTarget, setAsideFiles, allFiles, stackChoices]);



  // Put the saved view back once its target and files are ready: the file, the display mode, the overlays,
  // and (through the viewer) the zoom and the point at the center.
  React.useEffect(() => {
    if (!pendingRestore || isLoadingFiles) return;
    if (pendingRestore.target && selectedTarget !== pendingRestore.target) return;
    const saved = pendingRestore;
    const savedChoice = stackChoices.find((choice) => choice.stackedImage === saved.file);
    const fileKnown = saved.file !== null && (
      saved.file === stackedImage || saved.file === stackedSpectralTarget || Boolean(savedChoice)
      || allFiles.some((file) => file.path === saved.file)
    );
    if (saved.file && fileKnown) {
      // The saved file is the person's own choice and is not replaced by the default.
      pickedByUserRef.current = true;
      stackShownRef.current = true;
      setSelectedFile(saved.file);
      // A saved stack of another camera or lens is shown in the stack picker too.
      if (savedChoice) setChosenStackKey(savedChoice.isMain ? null : savedChoice.configurationKey);
    }
    if (saved.displayMode === 'linear' || saved.displayMode === 'auto-stretch') setDisplayMode(saved.displayMode as DisplayMode);
    setShowAstrometryOverlay(saved.astrometry);
    setDetectionsEnabled(saved.detections);
    latestViewRef.current = saved.view;
    if (saved.view && fileKnown) {
      restoreCountRef.current += 1;
      setRestoreView({ key: `restore-${restoreCountRef.current}`, view: saved.view });
    }
    setPendingRestore(null);
  }, [pendingRestore, isLoadingFiles, selectedTarget, allFiles, stackedImage, stackedSpectralTarget, stackChoices, setSelectedFile, setChosenStackKey, setDisplayMode]);

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
      collapsed={targetListCollapsed}
      onCollapsedChange={setTargetListCollapsed}
      hideLabel="Hide target list"
      showLabel="Show target list"
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
      stackOrientation={stackOrientation}
      blink={blink}
      shownFile={blink.shownPath}
      focusStar={blink.focusFor(loadedPath)}
      detectionsEnabled={detectionsEnabled}
      onDetectionsChange={setDetectionsEnabled}
      restoreView={restoreView}
      onViewChange={handleViewChange}
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
    />
  );

  const rightPanel = (
    <FrameAnalysisPanel
      allFiles={allFiles}
      filteredFiles={filteredFiles}
      checkedFiles={checkedFiles}
      stackedImage={stackedImage}
      stackedSpectralTarget={stackedSpectralTarget}
      stackConfiguration={stackConfiguration}
      stackChoices={stackChoices}
      viewedStack={viewedStack}
      viewedStackKey={viewedStackKey}
      onChooseStack={handleChooseStack}
      fileBrowserCamera={fileBrowserCamera}
      fileBrowserOptic={selectedOptic}
      handleSelectFile={pickFile}
      handleAddImages={handleAddImages}
      setIsAddModalOpen={setIsAddModalOpen}
      selectedFile={selectedFile}
      onShowHeader={handleOpenHeaderModal}
      starShapeReview={starShapeReview}
      frameReport={frameReport}
      frameStatuses={frameStatuses}
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
