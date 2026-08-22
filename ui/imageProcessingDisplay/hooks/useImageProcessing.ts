/**
 * @file useImageProcessing.ts
 * @description Orchestrator hook for image processing and analysis.
 * Decomposes logic into specialized hooks for stacking and analysis.
 */
import { useStackingJob, LightFrameRow } from './useStackingJob';
import { useAnalysisJob } from './useAnalysisJob';
import { ProcessingJob } from '../../common/types/backendTypes';

export { type LightFrameRow } from './useStackingJob';

/**
 * Unified result interface for both stacking and analysis operations.
 */
export interface UseImageProcessingResult {
    lightFrames: LightFrameRow[];
    logLines: string[];
    isProcessing: boolean;
    startProcessing: (imageFiles?: string[], logFile?: string) => Promise<void>;
    cancelProcessingJob: () => Promise<void>;
    clearLogs: () => void;
    refreshFrames: () => void;
    isAnalyzing: boolean;
    analysisResults: any;
    startAnalysis: (imageFiles?: string[], filterType?: string) => Promise<void>;
    jobHistory: ProcessingJob[];
    loadingHistory: boolean;
    activeJobId: string | null;
    onSelectJob: (job: ProcessingJob) => void;
    onDismissJob: (jobId: string) => Promise<void>;
    openSiril: () => Promise<void>;
}

/**
 * Custom hook to manage image processing operations and log streaming.
 * Orchestrates stacking and analysis jobs.
 * @param selectedTarget The ID of the currently selected target.
 * @param shouldFetch Whether to fetch data from the server.
 */
export function useImageProcessing(
    selectedTarget: string,
    shouldFetch: boolean = true
): UseImageProcessingResult {
    const stacking = useStackingJob(selectedTarget, shouldFetch);
    const analysis = useAnalysisJob(
        selectedTarget,
        shouldFetch,
        (line) => stacking.appendLog(line),
        (jobId) => stacking.setActiveJobId(jobId),
        () => stacking.clearLogs()
    );

    return {
        lightFrames: stacking.lightFrames,
        logLines: stacking.logLines,
        isProcessing: stacking.isProcessing,
        startProcessing: stacking.startProcessing,
        cancelProcessingJob: stacking.cancelProcessingJob,
        clearLogs: stacking.clearLogs,
        refreshFrames: stacking.refreshFrames,
        isAnalyzing: analysis.isAnalyzing,
        analysisResults: analysis.analysisResults,
        startAnalysis: analysis.startAnalysis,
        jobHistory: stacking.jobHistory,
        loadingHistory: stacking.loadingHistory,
        activeJobId: stacking.activeJobId,
        onSelectJob: stacking.onSelectJob,
        onDismissJob: stacking.onDismissJob,
        openSiril: stacking.openSiril
    };
}
