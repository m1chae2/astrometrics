import React, { useMemo, useState, useEffect } from 'react';
import { TabView } from '../../common/components/TabView';
import { SectionPanel } from '../../common/components/SectionPanel';
import { Tabs } from '../../common/components/Tabs';
import { PopoverSelect } from '../../common/components/PopoverSelect';

import { useCalibrationStats } from '../../common/hooks/useCalibrationStats';
import { useTargetContext } from '../../common/context/TargetContext';
import { ProcessingStatus } from './ProcessingStatus';
import { AnalysisResults } from '../analysisResults/AnalysisResults';
import { StackedImagePanel } from './StackedImagePanel';
import { UnifiedFrameAnalysis } from './FrameCountTable';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';
import { JobHistoryList } from './JobHistoryList';

import { useImageProcessingContext } from '../context/ImageProcessingContext';
import { fetchFrameStatsGrouped, refreshTarget, updateTargetById } from '../../common/services/targetService';
import { GroupedFrameStat } from '../../common/types/backendTypes';
import { chooseAnalysisSelection, chooseTemporalVariationSelection } from '../utils/analysisSelection';

interface FrameAnalysisPanelProps {
  // Data Tab Props
  allFiles: any[];
  filteredFiles: any[];
  checkedFiles: Set<string>;
  stackedImage: string | null;
  stackedSpectralTarget: string | null;
  totalExposure: number;
  handleSelectFile: (path: string | null) => void;

  // Control Props
  handleAddImages: () => void;
  setIsAddModalOpen: (open: boolean) => void;
  selectedFile: string | null;
  onShowHeader?: (path: string) => void;
}

/**
 * Component for the right-hand side panel of the Image Processing Display.
 * Handles frame analysis results, exposure statistics, and image controls.
 * REQ: IMG-1.2: The display SHALL show a summary of frame counts per filter/type.
 */
export const FrameAnalysisPanel: React.FC<FrameAnalysisPanelProps> = ({
  allFiles, filteredFiles, checkedFiles, stackedImage, stackedSpectralTarget, totalExposure,
  handleSelectFile,
  handleAddImages, setIsAddModalOpen,
  selectedFile, onShowHeader
}) => {
  const {
    selectedTarget, commonName, catalogId,
    setPendingTarget, forceReload,
    reloadKey
  } = useTargetContext();

    const {
      analysisResults, isProcessing, logLines, cancelProcessingJob, clearLogs,
      startProcessing, isAnalyzing, startAnalysis,
      jobHistory, loadingHistory, onSelectJob, targetActiveJobs, activeJobId,
      openSiril, requestJobHistory
    } = useImageProcessingContext();

  const { stats: calStats, loading: calLoading } = useCalibrationStats(reloadKey);
  const [activeAnalysisTab, setActiveAnalysisTab] = useState('data');
  // The job history only shows on the Analysis tab, so it is read when that tab is first opened.
  useEffect(() => {
    if (activeAnalysisTab === 'analysis') requestJobHistory?.();
  }, [activeAnalysisTab, requestJobHistory]);
  const [dataTabCamera, setDataTabCamera] = useState('All');
  const [lightStats, setLightStats] = useState<GroupedFrameStat[]>([]);

  // Compute unique cameras for the Data tab dropdown
  const uniqueCameras = useMemo(() => {
    const cams = new Set<string>();
    if (allFiles) {
      allFiles.forEach(f => cams.add(f.camera));
    }
    return Array.from(cams).sort();
  }, [allFiles]);

  // Fetch grouped stats from backend
  useEffect(() => {
    if (!selectedTarget) {
      setLightStats([]);
      return;
    }
    fetchFrameStatsGrouped(selectedTarget, dataTabCamera === 'All' ? undefined : dataTabCamera)
      .then(setLightStats)
      .catch(err => console.error("Failed to fetch grouped stats", err));
  }, [selectedTarget, dataTabCamera, reloadKey]);

  // Filter darks/biases/flats based on selected camera
  const filteredDarks = useMemo(() => {
    if (!calStats?.darks) return [];
    if (dataTabCamera === 'All') return calStats.darks;
    return calStats.darks.filter((d: any) => d.camera === dataTabCamera);
  }, [calStats, dataTabCamera]);

  const filteredBiases = useMemo(() => {
    if (!calStats?.biases) return [];
    if (dataTabCamera === 'All') return calStats.biases;
    return calStats.biases.filter((d: any) => d.camera === dataTabCamera);
  }, [calStats, dataTabCamera]);

  const filteredFlats = useMemo(() => {
    if (!calStats?.flats) return [];
    if (dataTabCamera === 'All') return calStats.flats;
    return calStats.flats.filter((d: any) => d.camera === dataTabCamera);
  }, [calStats, dataTabCamera]);

  const multiCameraStatus = useMemo(() => {
    const filesToConsider = checkedFiles.size > 0
      ? allFiles?.filter(f => checkedFiles.has(f.path)) || []
      : filteredFiles;
    if (filesToConsider.length === 0) return { hasMultiple: false, cameras: [] as string[] };
    const cameras = new Set<string>();
    filesToConsider.forEach(f => { if (f.camera) cameras.add(f.camera); });
    return { hasMultiple: cameras.size > 1, cameras: Array.from(cameras) };
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

  // What the two Analyze actions would send right now. Stage one analyzes the
  // master stacked spectral image; stage two follows the raw spectral frames.
  const selectionInput = {
    filterType: filterStatus.filterType,
    stackedImage,
    stackedSpectralTarget,
    checkedFiles,
    selectedFile,
    filteredFilePaths: filteredFiles.map(f => f.path),
  };
  const analysisSelection = chooseAnalysisSelection(selectionInput);
  const temporalVariationSelection = chooseTemporalVariationSelection(selectionInput);

  return (
    <div className="image-processing-display__right panel-group">
      <SectionPanel
        title="Frame Analysis"
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
                    exposureTime={totalExposure}
                    targetId={selectedTarget}
                    selectedFile={selectedFile}
                    onView={handleSelectFile}
                    onShowHeader={onShowHeader}
                    depthGoalRefreshKey={`${reloadKey}-${isProcessing ? 'busy' : 'idle'}`}
                  />
                  <AnalysisResults results={analysisResults} />
                  <UnifiedFrameAnalysis
                    lightFrames={lightStats}
                    darkFrames={filteredDarks}
                    biasFrames={filteredBiases}
                    flatFrames={filteredFlats}
                    isLoading={calLoading}
                    selectedCamera={dataTabCamera}
                    cameraSelector={
                      <div className="data-tab-filter" style={{ margin: 0 }}>
                        <PopoverSelect
                          label="Camera"
                          options={uniqueCameras.map((camera: string) => ({ id: camera, label: camera }))}
                          selectedId={dataTabCamera === 'All' ? null : dataTabCamera}
                          onSelect={(id) => setDataTabCamera(id ?? 'All')}
                          emptyLabel="All Cameras"
                          size="compact"
                        />
                      </div>
                    }
                  />
                </>
              )
            }
          ]}
        />
      </SectionPanel>
      <SectionPanel title="Image Controls" className="flex-auto">
        <ImageDetailsManager
          selectedTarget={selectedTarget}
          commonName={commonName}
          catalogId={catalogId}
          setPendingTarget={setPendingTarget}
          setReloadKey={forceReload}
          onProcess={filteredFiles.length > 0 ? () => {
            const filesToProcess = checkedFiles.size > 0
              ? Array.from(checkedFiles)
              : filteredFiles.map(f => f.path);
            startProcessing(filesToProcess);
          } : undefined}
          onCancel={cancelProcessingJob}
          onAddImages={handleAddImages}
          onAddTarget={() => setIsAddModalOpen(true)}
          isProcessing={isProcessing}
          hasMultipleCameras={multiCameraStatus.hasMultiple}
          selectedCameras={multiCameraStatus.cameras}
          hasNoFiles={checkedFiles.size === 0 && filteredFiles.length === 0}
          hasMultipleFilters={filterStatus.hasMultiple}
          selectedFilters={filterStatus.filters}
          onAnalyze={analysisSelection ? () => startAnalysis(analysisSelection.paths, analysisSelection.filterType) : undefined}
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
    </div>
  );
};
