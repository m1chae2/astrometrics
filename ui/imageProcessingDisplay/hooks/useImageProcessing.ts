/**
 * @file useImageProcessing.ts
 * @description Orchestrator hook for image processing and analysis.
 * Decomposes logic into specialized hooks for stacking and analysis.
 */
import { useStackingJob, LightFrameRow } from './useStackingJob';
import { useAnalysisJob } from './useAnalysisJob';
import type { AnalysisStageName } from '../../common/services/imaging/processingService';
import { ProcessingJob } from '../../common/types/backendTypes';

export { type LightFrameRow } from './useStackingJob';

/**
 * Unified result interface for both stacking and analysis operations.
 */
export interface UseImageProcessingResult {
    lightFrames: LightFrameRow[];
    isLoadingFrames: boolean;
    logLines: string[];
    isProcessing: boolean;
    startProcessing: (imageFiles?: string[], logFile?: string) => Promise<void>;
    cancelProcessingJob: () => Promise<void>;
    /** Stops the running analysis job (not the stacking job). */
    cancelAnalysisJob: () => Promise<void>;
    clearLogs: () => void;
    refreshFrames: () => void;
    isAnalyzing: boolean;
    /** Why the last stacking or analysis job failed (the job's own message), or `null`. */
    jobError: string | null;
    analysisResults: any;
    startAnalysis: (imageFiles?: string[], filterType?: string, stages?: AnalysisStageName[]) => Promise<void>;
    jobHistory: ProcessingJob[];
    loadingHistory: boolean;
    activeJobId: string | null;
    onSelectJob: (job: ProcessingJob) => void;
    targetActiveJobs: ProcessingJob[];
    openSiril: () => Promise<void>;
}

/**
 * Custom hook to manage image processing operations and log streaming.
 * Orchestrates stacking and analysis jobs.
 * @param selectedTarget The ID of the currently selected target.
 * @param shouldFetch Whether to fetch data from the server.
 * @param historyWanted Whether the job history is on screen; it is only read once it is.
 */
export function useImageProcessing(
    selectedTarget: string,
    shouldFetch: boolean = true,
    historyWanted: boolean = true
): UseImageProcessingResult {
    const stacking = useStackingJob(selectedTarget, shouldFetch, historyWanted);
    const analysis = useAnalysisJob(
        selectedTarget,
        shouldFetch,
        (line) => stacking.appendLog(line),
        (jobId) => stacking.setActiveJobId(jobId),
        () => stacking.clearLogs()
    );

    return {
        lightFrames: stacking.lightFrames,
        isLoadingFrames: stacking.isLoadingFrames,
        logLines: stacking.logLines,
        isProcessing: stacking.isProcessing,
        startProcessing: stacking.startProcessing,
        cancelProcessingJob: stacking.cancelProcessingJob,
        cancelAnalysisJob: analysis.cancelAnalysisJob,
        clearLogs: stacking.clearLogs,
        refreshFrames: stacking.refreshFrames,
        isAnalyzing: analysis.isAnalyzing,
        jobError: stacking.processingError ?? analysis.analysisError,
        analysisResults: analysis.analysisResults,
        startAnalysis: analysis.startAnalysis,
        jobHistory: stacking.jobHistory,
        loadingHistory: stacking.loadingHistory,
        activeJobId: stacking.activeJobId,
        onSelectJob: stacking.onSelectJob,
        targetActiveJobs: stacking.targetActiveJobs,
        openSiril: stacking.openSiril
    };
}
