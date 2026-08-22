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
    const { processing } = useAstrometrics();
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
            }
        };
        loadFrames();
        return () => { mounted = false; };
    }, [selectedTarget, shouldFetch, framesReloadKey]);

    // Global Process Monitoring
    useEffect(() => {
        let mounted = true;
        let timer: any = null;

        const checkStatus = async () => {
            if (!mounted || !selectedTarget) return;
            try {
                const jobs = await fetchJobs(selectedTarget);
                if (!mounted) return;
                setJobHistory(jobs);
                // Scoped to stacking jobs only: useAnalysisJob owns its own
                // isAnalyzing/activeAnalysisJobId state and log stream, so an
                // in-flight analysis job must not also flip stacking's
                // isProcessing (which would start a second, redundant poller
                // against the same job's log).
                const activeJob = jobs.find((j: any) => j.status === 'started' && j.jobType === 'stacking');
                const currentlyProcessing = !!activeJob;

                // Detect transition from processing to finished to trigger a refresh
                if (prevIsProcessing.current && !currentlyProcessing) {
                    invalidate('frames');
                }

                setIsProcessing(currentlyProcessing);
                prevIsProcessing.current = currentlyProcessing;

                if (activeJob) {
                    setActiveJobId(activeJob.id);
                }
            } catch { /* Ignore */ }
        };

        checkStatus();
        // Always poll, not just while a stacking job is active: jobHistory
        // must also pick up analysis (or other) jobs created out-of-band,
        // e.g. from a standalone script rather than this hook's own
        // startProcessing(). Poll less aggressively than the 2s stacking
        // cadence above since this covers the idle case too.
        timer = setInterval(checkStatus, isProcessing ? 2000 : 4000);

        return () => {
            mounted = false;
            if (timer) clearInterval(timer);
        };
    }, [selectedTarget, invalidate, isProcessing]);


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
