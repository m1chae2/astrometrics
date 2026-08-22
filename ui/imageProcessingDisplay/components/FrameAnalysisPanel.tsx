import React, { useMemo, useState, useEffect } from 'react';
import { TabView } from '../../common/components/TabView';
import { SectionPanel } from '../../common/components/SectionPanel';

import { useCalibrationStats } from '../hooks/useCalibrationStats';
import { useTargetContext } from '../../common/context/TargetContext';
import { ProcessingStatus } from './ProcessingStatus';
import { AnalysisResults } from '../analysisResults/AnalysisResults';
import { StackedImagePanel } from './StackedImagePanel';
import { UnifiedFrameAnalysis } from './FrameCountTable';
import { ImageDetailsManager } from '../imageDetailsManager/ImageDetailsManager';
import { JobHistoryList } from './JobHistoryList';

import { useImageProcessingContext } from '../context/ImageProcessingContext';
import { fetchFrameStatsGrouped, refreshTarget, updateTargetById } from '../../common/services/targetService';
import { ConfirmDialog } from '../../common/components/ConfirmDialog';
import { GroupedFrameStat } from '../../common/types/backendTypes';

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
  openIngestModal: () => void;
  remoteTargets: Set<string>;
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
  openIngestModal, remoteTargets, selectedFile, onShowHeader
}) => {
  const {
    selectedTarget, commonName, catalogId,
    setPendingTarget, forceReload,
    raShared, decShared,
    reloadKey
  } = useTargetContext();

    const {
      analysisResults, isProcessing, logLines, cancelProcessingJob, clearLogs,
      startProcessing, isAnalyzing, startAnalysis,
      jobHistory, loadingHistory, onSelectJob, onDismissJob, activeJobId,
      openSiril
    } = useImageProcessingContext();

  const { stats: calStats, loading: calLoading } = useCalibrationStats(reloadKey);
  const [activeAnalysisTab, setActiveAnalysisTab] = useState('data');
  const [dataTabCamera, setDataTabCamera] = useState('All');
  const [lightStats, setLightStats] = useState<GroupedFrameStat[]>([]);
  const [jobToDelete, setJobToDelete] = useState<string | null>(null);

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

  return (
    <div className="image-processing-display__right panel-group">
      <SectionPanel
        title="Frame Analysis"
        className="image-processing-display__analysis-section frame-analysis-panel flex-fill"
        headerContent={
          <div className="tab-view__header tab-view__header--inline" style={{ marginRight: '16px' }}>
            <button
              className={`tab-view__tab ${activeAnalysisTab === 'analysis' ? 'tab-view__tab--active' : ''}`}
              onClick={() => setActiveAnalysisTab('analysis')}
            >
              Analysis
            </button>
            <button
              className={`tab-view__tab ${activeAnalysisTab === 'data' ? 'tab-view__tab--active' : ''}`}
              onClick={() => setActiveAnalysisTab('data')}
            >
              Data
            </button>
          </div>
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
                    jobs={jobHistory}
                    loading={loadingHistory}
                    onSelectJob={onSelectJob}
                    onDismissJob={async (jobId) => setJobToDelete(jobId)}
                    activeJobId={activeJobId}
                  />
                  {jobToDelete && (
                    <ConfirmDialog
                      open={!!jobToDelete}
                      title="Dismiss Job"
                      message="Are you sure you want to dismiss this job? This will permanently remove its record."
                      onConfirm={async () => {
                        await onDismissJob(jobToDelete);
                        setJobToDelete(null);
                      }}
                      onCancel={() => setJobToDelete(null)}
                      confirmLabel="Dismiss"
                    />
                  )}
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
                    onView={handleSelectFile}
                    onShowHeader={onShowHeader}
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
                        <select
                          className="file-browser__camera-select"
                          value={dataTabCamera}
                          onChange={(e) => setDataTabCamera(e.target.value)}
                        >
                          <option value="All">All Cameras</option>
                          {uniqueCameras.map(c => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
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
          ra={raShared}
          dec={decShared}
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
          onAnalyze={(stackedImage || stackedSpectralTarget || checkedFiles.size > 0 || selectedFile || filteredFiles.length > 0) ? () => {
            // Priority 1: Specifically checked files (checkboxes)
            if (checkedFiles.size > 0) {
              startAnalysis(Array.from(checkedFiles), filterStatus.filterType || undefined);
            }
            // Priority 2: Stacked spectral results
            else if ((filterStatus.filterType === 'SPEC' || filterStatus.filterType === 'Spectroscopy' || filterStatus.filterType === 'Star Analyzer 200') && stackedSpectralTarget) {
              startAnalysis([stackedSpectralTarget], 'SPEC');
            }
            // Priority 3: Stacked images (L or other)
            else if (stackedImage) {
              startAnalysis([stackedImage], filterStatus.filterType || undefined);
            }
            // Priority 4: Single selected (previewed) file
            else if (selectedFile) {
              startAnalysis([selectedFile], filterStatus.filterType || undefined);
            }
            // Priority 5: All files in current view
            else {
              const filesToAnalyze = filteredFiles.map(f => f.path);
              startAnalysis(filesToAnalyze, filterStatus.filterType || undefined);
            }
          } : undefined}
          isAnalyzing={isAnalyzing}
          onIngest={openIngestModal}
          isIngestDisabled={!selectedTarget}
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
