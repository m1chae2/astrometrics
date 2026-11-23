/**
 * @file useStackingJob.ts
 * @description Hook for managing image stacking jobs and frame summaries.
 */
import { useState, useEffect, useMemo, useRef } from 'react';
import { processTarget, cancelProcessing, fetchAllProcesses, fetchJobs, openSiril } from '../../common/services/imaging/processingService';
import {
    GroupedFrameStat,
    ProcessStatus,
    ProcessingJob
} from '../../common/types/backendTypes';
import { fetchFrameStatsGrouped } from '../../common/services/targetService';
import { reportError } from '../../common/utils/reportError';
import { emitToast } from '../../common/utils/emitToast';
import { useTargetContext } from '../../common/context/TargetContext';
import { useOnValueChange } from './useOnValueChange';
import { useAstrometrics } from '../../common/context/AstrometricsContext';
import { useJobLog } from './useJobLog';
import { useJobEndWatcher } from './useJobEndWatcher';
import { announceFinishedStack } from '../utils/announceFinishedStack';

export type LightFrameRow = [string, string, number];

export interface StackingJobResult {
    lightFrames: LightFrameRow[];
    /** Whether the light-frame summary for the selected target is still being fetched. */
    isLoadingFrames: boolean;
    logLines: string[];
    isProcessing: boolean;
    /** Why the last stacking job failed (its own message), or `null`. */
    processingError: string | null;
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
    /** Jobs of any type for the selected target that are running right now (from the app-wide active-jobs feed). */
    targetActiveJobs: ProcessingJob[];
    openSiril: () => Promise<void>;
}

/**
 * Keeps one target's stacking state: its light frames, the running job, its log
 * and (once asked for) the job history.
 *
 * @param selectedTarget The target to follow.
 * @param shouldFetch Whether to read from the library at all (false for a target that is not local).
 * @param historyWanted Whether the job history and the last job's log are on screen (the Analysis
 *   tab). They are only read once this is true, because nothing else shows them.
 */
export function useStackingJob(
    selectedTarget: string,
    shouldFetch: boolean = true,
    historyWanted: boolean = true
): StackingJobResult {
    const { framesReloadKey, invalidate } = useTargetContext();
    const { activeJobs } = useAstrometrics();
    const [lightFrames, setLightFrames] = useState<LightFrameRow[]>([]);
    const [isProcessing, setIsProcessing] = useState<boolean>(false);
    const [processingError, setProcessingError] = useState<string | null>(null);
    /** The job this hook started and is watching to its end. */
    const [watchedJobId, setWatchedJobId] = useState<string | null>(null);
    const [activeJobId, setActiveJobId] = useState<string | null>(null);
    const [jobHistory, setJobHistory] = useState<ProcessingJob[]>([]);
    const [loadingHistory, setLoadingHistory] = useState<boolean>(false);
    // The app-wide active-jobs feed (AstrometricsContext `activeJobs`) is the
    // one signal that a job started or finished. Jobs of any type for this
    // target that are running right now are shown at the top of the list.
    const targetActiveJobs = useMemo(
        () => activeJobs.filter((job) => job.targetId === selectedTarget),
        [activeJobs, selectedTarget]
    );
    const targetActiveJobIds = useMemo(
        () => targetActiveJobs.map((job) => job.id).sort().join(','),
        [targetActiveJobs]
    );
    // A job is running when the app-wide feed lists it or its record says it started. This
    // looks at the job record only, never at the kind of job.
    const followedJobIsRunning = activeJobId !== null && (
        targetActiveJobs.some((job) => job.id === activeJobId)
        || jobHistory.some((job) => job.id === activeJobId && job.status === 'started')
        || isProcessing
    );
    /** Shows the log of the job on screen: live while it runs, whole when it is over. */
    const jobLog = useJobLog(activeJobId, followedJobIsRunning);
    const setLogLines = (lines: string[]) => { if (lines.length === 0) jobLog.clear(); };
    const prevIsProcessing = useRef<boolean>(false);
    /** The id of the stacking job the running-jobs feed showed last, to announce it when it ends. */
    const lastFeedJobId = useRef<string | null>(null);
    const isProcessingRef = useRef<boolean>(false);
    /** The running-job ids seen last time, to notice when one starts or finishes. */
    const previousActiveJobIds = useRef<string>('');
    /** Ids of running jobs already seen for this target, so each new one is followed once. */
    const followedStartedJobIds = useRef<Set<string>>(new Set());

    /** The target whose job history and log were already read, so a refresh keeps the shown log. */
    const historyLoadedForTarget = useRef<string | null>(null);

    // Clear state only when the target really changes, not when the display is shown again.
    useOnValueChange(selectedTarget, () => {
        setLogLines([]);
        setLightFrames([]);
        setIsProcessing(false);
        setProcessingError(null);
        setWatchedJobId(null);
        prevIsProcessing.current = false;
        lastFeedJobId.current = null;
        previousActiveJobIds.current = '';
        followedStartedJobIds.current = new Set();
        historyLoadedForTarget.current = null;
    });

    // Read the job history and the latest job's log, but only once the Analysis
    // tab (the only place they show) has been opened. Without this, every open of
    // the display paid for two reads nobody could see.
    useEffect(() => {
        if (!selectedTarget || !historyWanted) return;
        let mounted = true;
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
            // When the display is shown again, only the list is refreshed. The log the
            // user picked and the job being followed stay as they are.
            if (historyLoadedForTarget.current === selectedTarget) return;
            historyLoadedForTarget.current = selectedTarget;
            if (latestJob) {
                setIsProcessing(latestJob.status === 'started');
                setActiveJobId(latestJob.id);
            } else {
                setActiveJobId(null);
            }
        }).catch(err => {
            if (mounted) setLoadingHistory(false);
            console.warn("Failed to fetch jobs for target:", err);
        });
        return () => { mounted = false; };
    }, [selectedTarget, historyWanted]);

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
            // Tell the viewers to read the restacked picture (once per job, whoever sees the end).
            if (lastFeedJobId.current) void announceFinishedStack(selectedTarget, lastFeedJobId.current);
        }
        if (activeJob) lastFeedJobId.current = activeJob.id;

        setIsProcessing(currentlyProcessing);
        prevIsProcessing.current = currentlyProcessing;

        if (activeJob) {
            setActiveJobId(activeJob.id);
        } else {
            // A job of another kind (preview, check-stack, analysis) that has just started is
            // followed too, once, so its log shows live without a click.
            const started = activeJobs.find(
                (j) => j.targetId === selectedTarget && !followedStartedJobIds.current.has(j.id)
            );
            if (started) setActiveJobId(started.id);
        }
        for (const job of activeJobs) {
            if (job.targetId === selectedTarget) followedStartedJobIds.current.add(job.id);
        }
    }, [selectedTarget, invalidate, activeJobs]);


    // Reload the history whenever the set of running jobs changes: a new id
    // means a job started, a missing id means one finished. The first value
    // after a target change is skipped because the target-change effect
    // above already loads the history.
    useEffect(() => {
        if (!selectedTarget) return;
        const previous = previousActiveJobIds.current;
        previousActiveJobIds.current = targetActiveJobIds;
        if (previous === targetActiveJobIds) return;
        // Until the history is on screen it is not kept up to date; it is read
        // when it is first wanted.
        if (!historyWanted) return;
        let mounted = true;
        fetchJobs(selectedTarget).then((jobs) => {
            if (mounted) setJobHistory(jobs);
        }).catch(() => { /* Ignore */ });
        return () => { mounted = false; };
    }, [selectedTarget, targetActiveJobIds, historyWanted]);

    useEffect(() => {
        isProcessingRef.current = isProcessing;
    }, [isProcessing]);

    // A job that fails at once never shows in the running-jobs feed, so the effect above cannot
    // see it end. Watch the job itself, and show why it failed.
    useJobEndWatcher(watchedJobId, (job) => {
        setWatchedJobId(null);
        if (job.status === 'failed') {
            setProcessingError(job.message || 'The stacking job failed.');
        }
        if (prevIsProcessing.current || isProcessingRef.current) {
            invalidate('frames');
        }
        void announceFinishedStack(selectedTarget, job.id, job);
        prevIsProcessing.current = false;
        setIsProcessing(false);
    });


    const startProcessing = async (imageFiles?: string[], logFile?: string) => {
        if (!selectedTarget) return;
        setLogLines([]);
        // Forget the previous job so its log is not streamed while we wait
        // for the new job's id.
        setActiveJobId(null);
        setProcessingError(null);
        setIsProcessing(true);
        try {
            const result = await processTarget(selectedTarget, imageFiles, logFile);
            if (result && result.jobId) {
                setActiveJobId(result.jobId);
                setWatchedJobId(result.jobId);
            }
        } catch (err) {
            setIsProcessing(false);
            reportError(err, 'startProcessing');
        }
    };

    const cancelProcessingJob = async () => {
        if (!selectedTarget) return;
        try {
            const result = await cancelProcessing(selectedTarget);
            // A job that runs in another program keeps running: say so and keep showing it.
            if (result && result.status === 'other_process' && result.message) {
                emitToast(result.message, 'warning', 'cancelProcessingJob');
                return;
            }
            setIsProcessing(false);
        } catch (err) { /* Reported by API */ }
    };

    return {
        lightFrames,
        isLoadingFrames,
        logLines: jobLog.lines,
        isProcessing,
        processingError,
        startProcessing,
        cancelProcessingJob,
        clearLogs: () => setLogLines([]),
        appendLog: jobLog.appendLocal,
        refreshFrames: () => invalidate('frames'),
        jobHistory,
        loadingHistory,
        onSelectJob: (job: ProcessingJob) => {
            setActiveJobId(job.id);
        },
        activeJobId,
        setActiveJobId,
        targetActiveJobs,
        openSiril: async () => {
            if (!selectedTarget) return;
            await openSiril(selectedTarget);
        }
    };
}
