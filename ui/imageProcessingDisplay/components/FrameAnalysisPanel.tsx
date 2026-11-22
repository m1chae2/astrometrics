import React, { useMemo, useState, useEffect } from 'react';
import { TabView } from '../../common/components/TabView';
import { SectionPanel } from '../../common/components/SectionPanel';
import { Tabs } from '../../common/components/Tabs';

import { useTargetContext } from '../../common/context/TargetContext';
import { ProcessingStatus } from './ProcessingStatus';
import { AnalysisResults } from '../analysisResults/AnalysisResults';
import { StackedImagePanel } from './StackedImagePanel';
import { CalibrationLine } from './CalibrationLine';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';
import { JobHistoryList } from './JobHistoryList';
import { StarShapesSummary } from './StarShapesSummary';

import { useImageProcessingContext } from '../context/ImageProcessingContext';
import { fetchFrameStatsGrouped, refreshTarget, updateTargetById } from '../../common/services/targetService';
import { FrameStatusReport, GroupedFrameStat, StarShapeReview } from '../../common/types/backendTypes';
import type { FrameStatusInfo } from '../utils/frameStatus';
import { getMultiOpticStatus } from '../utils/opticSelection';
import { chooseAnalysisSelection, chooseStageSelection, chooseTemporalVariationSelection, NO_SOURCE_TO_ANALYZE_REASON, type AnalysisStage } from '../utils/analysisSelection';

interface FrameAnalysisPanelProps {
  // Data Tab Props
  allFiles: any[];
  filteredFiles: any[];
  checkedFiles: Set<string>;
  stackedImage: string | null;
  stackedSpectralTarget: string | null;
  handleSelectFile: (path: string | null) => void;

  // Control Props
  handleAddImages: () => void;
  setIsAddModalOpen: (open: boolean) => void;
  selectedFile: string | null;
  onShowHeader?: (path: string) => void;
  /** The stored star-shape verdicts of the target, or `null` when not available. */
  starShapeReview?: StarShapeReview | null;
  /** The backend's per-frame statuses, including set-aside frames, or `null` until read. */
  frameReport?: FrameStatusReport | null;
  /** The File Browser's status of each frame by path. */
  frameStatuses?: ReadonlyMap<string, FrameStatusInfo>;

  // Fold state, held by the screen because the Image Controls tab can sit in the viewer's bottom bar.
  analysisCollapsed: boolean;
  setAnalysisCollapsed: (collapsed: boolean) => void;
  controlsCollapsed: boolean;
  setControlsCollapsed: (collapsed: boolean) => void;
}

/**
 * Component for the right-hand side panel of the Image Processing Display.
 * Handles frame analysis results, exposure statistics, and image controls.
 */
export const FrameAnalysisPanel: React.FC<FrameAnalysisPanelProps> = ({
  allFiles, filteredFiles, checkedFiles, stackedImage, stackedSpectralTarget,
  handleSelectFile,
  handleAddImages, setIsAddModalOpen,
  selectedFile, onShowHeader, starShapeReview = null, frameReport = null, frameStatuses,
  analysisCollapsed, setAnalysisCollapsed, controlsCollapsed, setControlsCollapsed
}) => {
  const {
    selectedTarget, commonName, catalogId,
    setPendingTarget, forceReload,
    reloadKey
  } = useTargetContext();

    const {
      analysisResults, isProcessing, logLines, cancelProcessingJob, clearLogs,
      startProcessing, isAnalyzing, startAnalysis, jobError,
      jobHistory, loadingHistory, onSelectJob, targetActiveJobs, activeJobId,
      openSiril, requestJobHistory
    } = useImageProcessingContext();

  const [activeAnalysisTab, setActiveAnalysisTab] = useState('data');
  // The job history only shows on the Analysis tab, so it is read when that tab is first opened.
  useEffect(() => {
    if (activeAnalysisTab === 'analysis') requestJobHistory?.();
  }, [activeAnalysisTab, requestJobHistory]);
  const [lightStats, setLightStats] = useState<GroupedFrameStat[]>([]);

  // Fetch grouped stats from backend
  useEffect(() => {
    if (!selectedTarget) {
      setLightStats([]);
      return;
    }
    fetchFrameStatsGrouped(selectedTarget)
      .then(setLightStats)
      .catch(err => console.error("Failed to fetch grouped stats", err));
  }, [selectedTarget, reloadKey]);

  const multiCameraStatus = useMemo(() => {
    const filesToConsider = checkedFiles.size > 0
      ? allFiles?.filter(f => checkedFiles.has(f.path)) || []
      : filteredFiles;
    if (filesToConsider.length === 0) return { hasMultiple: false, cameras: [] as string[] };
    const cameras = new Set<string>();
    filesToConsider.forEach(f => { if (f.camera) cameras.add(f.camera); });
    return { hasMultiple: cameras.size > 1, cameras: Array.from(cameras) };
  }, [checkedFiles, allFiles, filteredFiles]);

  // Frames from more than one optic cannot be stacked together.
  const multiOpticStatus = useMemo(() => {
    const filesToConsider = checkedFiles.size > 0
      ? allFiles?.filter(f => checkedFiles.has(f.path)) || []
      : filteredFiles;
    return getMultiOpticStatus(filesToConsider);
  }, [checkedFiles, allFiles, filteredFiles]);

  const filterStatus = useMemo(() => {
    const filesToConsider = checkedFiles.size > 0
      ? allFiles?.filter(f => checkedFiles.has(f.path)) || []
      : filteredFiles;
    if (filesToConsider.length === 0) return { hasMultiple: false, filterType: null, filters: [] as string[] };
    const filters = new Set(filesToConsider.map(f => f.filter));
    return {
      hasMultiple: filters.size > 1,
      filterType: filters.size === 1 ? Array.from(filters)[0] : null,
      filters: Array.from(filters)
    };
  }, [checkedFiles, allFiles, filteredFiles]);

  // The frames the Stack Frames click sends: the ticked frames, or every listed frame when none is
  // ticked. Check Frames checks the same frames.
  const framesToStack = (): string[] =>
    checkedFiles.size > 0 ? Array.from(checkedFiles) : filteredFiles.map(f => f.path);

  // What the two Analyze actions would send right now. Stage one analyzes the
  // master stacked spectral image; stage two follows the raw spectral frames.
  const selectionInput = {
    filterType: filterStatus.filterType,
    stackedImage,
    stackedSpectralTarget,
    checkedFiles,
    selectedFile,
    filteredFilePaths: filteredFiles.map(f => f.path),
    selectedFileFilter: selectedFile
      ? (allFiles?.find(f => f.path === selectedFile)?.filter ?? null)
      : null,
  };
  const analysisSelection = chooseAnalysisSelection(selectionInput);
  const temporalVariationSelection = chooseTemporalVariationSelection(selectionInput);

  // Frame Analysis folded: a thin rail on the right edge.
  const analysisRail = (
    <SectionPanel
      title="Frame Analysis"
      collapsed="right"
      onTitleClick={() => setAnalysisCollapsed(false)}
      titleButtonLabel="Expand Frame Analysis"
    >
      {null}
    </SectionPanel>
  );

  // Frame Analysis folded: the whole right column, Image Controls too, is just the rail.
  if (analysisCollapsed) {
    return <div className="image-processing-display__right image-processing-display__right--collapsed">{analysisRail}</div>;
  }

  return (
    <div className="image-processing-display__right panel-group">
      <SectionPanel
        title="Frame Analysis"
        onTitleClick={() => setAnalysisCollapsed(true)}
        titleButtonLabel="Collapse to the right"
        className="image-processing-display__analysis-section frame-analysis-panel flex-fill"
        headerContent={
          <Tabs
            className="frame-analysis-panel__tabs"
            tabs={[
              { id: 'analysis', label: 'Analysis' },
              { id: 'data', label: 'Data' },
            ]}
            selectedId={activeAnalysisTab}
            onChange={setActiveAnalysisTab}
            ariaLabel="Frame Analysis views"
            idPrefix="frame-analysis"
          />
        }
      >
        <TabView
          noHeader={true}
          activeTabId={activeAnalysisTab}
          tabs={[
            {
              id: 'analysis',
              label: 'Analysis',
              content: (
                <>

                  <ProcessingStatus
                    isProcessing={isProcessing || isAnalyzing}
                    logLines={logLines}
                    onCancel={isProcessing ? cancelProcessingJob : undefined}
                    onClear={clearLogs}
                  />
                  <JobHistoryList
                    targetId={selectedTarget}
                    jobs={jobHistory}
                    pinnedJobs={targetActiveJobs}
                    loading={loadingHistory}
                    onSelectJob={onSelectJob}
                    activeJobId={activeJobId}
                  />
                </>
              )
            },
            {
              id: 'data',
              label: 'Data',
              content: (
                <>
                  <StackedImagePanel
                    path={stackedImage || ''}
                    spectralPath={stackedSpectralTarget || ''}
                    targetId={selectedTarget}
                    selectedFile={selectedFile}
                    onView={handleSelectFile}
                    onShowHeader={onShowHeader}
                    depthGoalRefreshKey={`${reloadKey}-${isProcessing ? 'busy' : 'idle'}`}
                  />
                  <AnalysisResults results={analysisResults} />
                  <StarShapesSummary
                    review={starShapeReview}
                    frameReport={frameReport}
                    frameStatuses={frameStatuses}
                    selectedPath={selectedFile}
                    onSelectFrame={handleSelectFile}
                  />
                  <CalibrationLine lightFrames={lightStats} />
                </>
              )
            }
          ]}
        />
      </SectionPanel>
      {controlsCollapsed ? (
        <SectionPanel
          title="Image Controls"
          collapsed="down"
          onTitleClick={() => setControlsCollapsed(false)}
          titleButtonLabel="Expand Image Controls"
        >
          {null}
        </SectionPanel>
      ) : (
      <SectionPanel
        title="Image Controls"
        className="flex-auto"
        onTitleClick={() => setControlsCollapsed(true)}
        titleButtonLabel="Collapse down"
      >
        <ImageDetailsManager
          selectedTarget={selectedTarget}
          commonName={commonName}
          catalogId={catalogId}
          setPendingTarget={setPendingTarget}
          setReloadKey={forceReload}
          onProcess={filteredFiles.length > 0 ? () => startProcessing(framesToStack()) : undefined}
          framesToStack={filteredFiles.length > 0 ? framesToStack : undefined}
          onCancel={cancelProcessingJob}
          onShowPicture={handleSelectFile}
          onAddImages={handleAddImages}
          onAddTarget={() => setIsAddModalOpen(true)}
          isProcessing={isProcessing}
          jobError={jobError}
          hasMultipleCameras={multiCameraStatus.hasMultiple}
          selectedCameras={multiCameraStatus.cameras}
          hasMultipleOptics={multiOpticStatus.hasMultiple}
          selectedOptics={multiOpticStatus.focalLengths}
          hasNoFiles={checkedFiles.size === 0 && filteredFiles.length === 0}
          hasMultipleFilters={filterStatus.hasMultiple}
          selectedFilters={filterStatus.filters}
          onAnalyze={() => {
            if (analysisSelection) startAnalysis(analysisSelection.paths, analysisSelection.filterType);
          }}
          analyzeDisabledReason={analysisSelection ? undefined : NO_SOURCE_TO_ANALYZE_REASON}
          analysisSelectionInput={selectionInput}
          onAnalyzeStage={(stage: AnalysisStage) => {
            const stageSelection = chooseStageSelection(selectionInput, stage);
            if (stageSelection) startAnalysis(stageSelection.paths, stageSelection.filterType, [stage]);
          }}
          onAnalyzeTemporalVariation={temporalVariationSelection
            ? () => startAnalysis(temporalVariationSelection.paths, temporalVariationSelection.filterType)
            : undefined}
          isAnalyzing={isAnalyzing}
          onOpenSiril={() => {
            openSiril();
          }}
          onReindex={selectedTarget ? async () => {
            await refreshTarget(selectedTarget, true);
            forceReload();
          } : undefined}
          onSetStackedProperty={async (property, filePath) => {
            if (selectedTarget && filePath) {
              try {
                await updateTargetById(selectedTarget, { [property]: filePath });
                forceReload();
              } catch (err) {
                console.error("Failed to set stacked property", err);
              }
            }
          }}
        />
      </SectionPanel>
      )}
    </div>
  );
};
