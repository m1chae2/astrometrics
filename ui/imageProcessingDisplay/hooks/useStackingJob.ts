/**
 * @file useStackingJob.ts
 * @description Hook for managing image stacking jobs and frame summaries.
 * REQ: IMG-1.2: The display SHALL show a summary of frame counts.
 * REQ: IMG-5.1: The display SHALL provide a "Process Target" command.
 */
import { useState, useEffect, useRef } from 'react';
import {
    processTarget,
    cancelProcessing,
    fetchAllProcesses,
    fetchJobs,
    fetchJobLogTail,
    streamJobLog,
    dismissJob,
    openSiril
} from '../../common/services/imagingService';
import {
    GroupedFrameStat,
    ProcessStatus,
    ProcessingJob
} from '../../common/types/backendTypes';
import { fetchFrameStatsGrouped } from '../../common/services/targetService';
import { reportError } from '../../common/utils/reportError';
import { useTargetContext } from '../../common/context/TargetContext';
import { useAstrometrics } from '../../common/context/AstrometricsContext';

export type LightFrameRow = [string, string, number];

export interface StackingJobResult {
    lightFrames: LightFrameRow[];
    /** Whether the light-frame summary for the selected target is still being fetched. */
    isLoadingFrames: boolean;
    logLines: string[];
    isProcessing: boolean;
    startProcessing: (imageFiles?: string[], logFile?: string) => Promise<void>;
    cancelProcessingJob: () => Promise<void>;
    clearLogs: () => void;
    appendLog: (line: string) => void;
    refreshFrames: () => void;
    jobHistory: ProcessingJob[];
    loadingHistory: boolean;
    onSelectJob: (job: ProcessingJob) => void;
    activeJobId: string | null;
    setActiveJobId: (jobId: string | null) => void;
    onDismissJob: (jobId: string) => Promise<void>;
    openSiril: () => Promise<void>;
}

export function useStackingJob(
    selectedTarget: string,
    shouldFetch: boolean = true
): StackingJobResult {
    const { framesReloadKey, invalidate } = useTargetContext();
    const { activeJobs } = useAstrometrics();
    const [lightFrames, setLightFrames] = useState<LightFrameRow[]>([]);
    const [logLines, setLogLines] = useState<string[]>([]);
    const [isProcessing, setIsProcessing] = useState<boolean>(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(null);
    const [jobHistory, setJobHistory] = useState<ProcessingJob[]>([]);
    const [loadingHistory, setLoadingHistory] = useState<boolean>(false);
    const prevIsProcessing = useRef<boolean>(false);

    // Clear state when target changes and fetch history
    useEffect(() => {
        let mounted = true;
        setLogLines([]);
        setLightFrames([]);
        setIsProcessing(false);
        prevIsProcessing.current = false;

        if (selectedTarget) {
            setLoadingHistory(true);
            // First, try to find the latest job for this target
            fetchJobs(selectedTarget).then(jobs => {
                if (!mounted) return;
                setJobHistory(jobs);
                setLoadingHistory(false);
                // jobs are sorted by createdAt DESC in backend; scope to
                // stacking so an in-flight analysis job for this target
                // isn't mistaken for a stacking job (see Global Process
                // Monitoring below for why that scoping matters).
                const latestJob = jobs.find(j => j.jobType === 'stacking');
                if (latestJob) {
                    setIsProcessing(latestJob.status === 'started');
                    setActiveJobId(latestJob.id);
                    fetchJobLogTail(latestJob.id, 5000).then(lines => {
                        if (mounted && lines.length > 0) {
                            setLogLines(lines);
                        }
                    });
                } else {
                    setActiveJobId(null);
                }
            }).catch(err => {
                setLoadingHistory(false);
                console.warn("Failed to fetch jobs for target:", err);
            });
        }

        return () => { mounted = false; };
    }, [selectedTarget]);

    // Identifies which fetch of the light-frame summary is currently wanted:
    // null means "nothing to fetch" (no target selected, or `shouldFetch` --
    // typically whether the target is confirmed local -- is false); a string
    // means a fetch for that target+reload-generation is wanted. Comparing
    // this against `settledFramesKey` below gives `isLoadingFrames` purely
    // from this render's own values, with no dependency on a *separate*
    // piece of state that this hook's own effect would otherwise have to
    // "catch up" to a render late. That matters because `shouldFetch` itself
    // often flips from false to true only once a prerequisite (e.g. the
    // shared target list) finishes loading elsewhere -- if `isLoadingFrames`
    // were instead toggled by this hook's effect, callers gating "fully
    // loaded" on it (see ImageProcessingDisplay.tsx's app boot readiness
    // check) could read a stale "not loading" value for one render after
    // `shouldFetch` flips true but before this hook's effect has run again.
    const pendingFramesKey = selectedTarget && shouldFetch ? `${selectedTarget}:${framesReloadKey}` : null;
    const [settledFramesKey, setSettledFramesKey] = useState<string | null>(null);
    const isLoadingFrames = pendingFramesKey !== null && settledFramesKey !== pendingFramesKey;

    // Fetch and parse light frames
    useEffect(() => {
        let mounted = true;
        const loadFrames = async () => {
            if (!selectedTarget || !shouldFetch) {
                if (mounted) setLightFrames([]);
                return;
            }
            try {
                const stats = await fetchFrameStatsGrouped(selectedTarget);
                if (!mounted) return;

                const rows: LightFrameRow[] = stats.map(s => [
                    s.iso || 'Unknown',
                    s.exposure || '0',
                    s.count
                ]);

                if (mounted) setLightFrames(rows);
            } catch (err) {
                if (mounted) reportError(err, 'useStackingJob');
            } finally {
                if (mounted) setSettledFramesKey(`${selectedTarget}:${framesReloadKey}`);
            }
        };
        loadFrames();
        return () => { mounted = false; };
    }, [selectedTarget, shouldFetch, framesReloadKey]);

    // Global Process Monitoring: derived from AstrometricsContext's shared
    // active-jobs feed (one poll for the whole app) instead of this hook
    // running its own competing poll against the same backend data. Still
    // catches jobs started out-of-band (e.g. a standalone script), since the
    // context's feed isn't scoped to jobs this hook itself started.
    useEffect(() => {
        if (!selectedTarget) return;

        // Scoped to stacking jobs only: useAnalysisJob owns its own
        // isAnalyzing/activeAnalysisJobId state and log stream, so an
        // in-flight analysis job must not also flip stacking's isProcessing
        // (which would start a second, redundant poller against the same
        // job's log).
        const activeJob = activeJobs.find((j) => j.targetId === selectedTarget && j.jobType === 'stacking');
        const currentlyProcessing = !!activeJob;

        // Detect transition from processing to finished to trigger a refresh
        // of frames and job history (a one-off fetch, not a recurring poll).
        if (prevIsProcessing.current && !currentlyProcessing) {
            invalidate('frames');
            fetchJobs(selectedTarget).then(setJobHistory).catch(() => { /* Ignore */ });
        }

        setIsProcessing(currentlyProcessing);
        prevIsProcessing.current = currentlyProcessing;

        if (activeJob) {
            setActiveJobId(activeJob.id);
        }
    }, [selectedTarget, invalidate, activeJobs]);


    // Log Streaming
    useEffect(() => {
        let cancelStream: (() => void) | undefined;
        let cancelled = false;
        if (isProcessing && activeJobId) {
            streamJobLog(activeJobId, (line) => {
                setLogLines((prev) => {
                    if (prev.length > 0 && prev[prev.length - 1] === line) return prev;
                    return [...prev, line];
                });
            }).then((cancel) => {
                if (cancelled) {
                    cancel();
                } else {
                    cancelStream = cancel;
                }
            });
        }
        return () => {
            cancelled = true;
            if (cancelStream) cancelStream();
        };
    }, [isProcessing, activeJobId]);

    // Auto-dismissal removed per user request

    const startProcessing = async (imageFiles?: string[], logFile?: string) => {
        if (!selectedTarget) return;
        setLogLines([]);
        setIsProcessing(true);
        try {
            const result = await processTarget(selectedTarget, imageFiles, logFile);
            if (result && result.jobId) {
                setActiveJobId(result.jobId);
            }
        } catch (err) {
            setIsProcessing(false);
            reportError(err, 'startProcessing');
        }
    };

    const cancelProcessingJob = async () => {
        if (!selectedTarget) return;
        try {
            await cancelProcessing(selectedTarget);
            setIsProcessing(false);
        } catch (err) { /* Reported by API */ }
    };

    return {
        lightFrames,
        isLoadingFrames,
        logLines,
        isProcessing,
        startProcessing,
        cancelProcessingJob,
        clearLogs: () => setLogLines([]),
        appendLog: (line: string) => setLogLines(prev => [...prev, line]),
        refreshFrames: () => invalidate('frames'),
        jobHistory,
        loadingHistory,
        onSelectJob: (job: ProcessingJob) => {
            setActiveJobId(job.id);
            setLogLines([]);
            fetchJobLogTail(job.id, 5000).then(setLogLines);
        },
        activeJobId,
        setActiveJobId,
        onDismissJob: async (jobId: string) => {
            const success = await dismissJob(jobId);
            if (success) {
                setJobHistory(prev => prev.filter(j => j.id !== jobId));
                if (activeJobId === jobId) {
                    setActiveJobId(null);
                    setLogLines([]);
                }
            }
        },
        openSiril: async () => {
            if (!selectedTarget) return;
            await openSiril(selectedTarget);
        }
    };
}
