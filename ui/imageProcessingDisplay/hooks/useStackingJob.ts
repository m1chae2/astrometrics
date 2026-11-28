/**
 * @file useStackingJob.ts
 * @description Hook for managing image stacking jobs and frame summaries.
 */
import { useState, useEffect, useMemo, useRef } from 'react';
import { processTarget, cancelProcessing, fetchAllProcesses, fetchJobs, openSiril } from '../../common/services/imaging/processingService';
import {
    ProcessStatus,
    ProcessingJob
} from '../../common/types/backendTypes';
import { useGroupedFrameStats } from './useGroupedFrameStats';
import { reportError } from '../../common/utils/reportError';
import { emitToast } from '../../common/utils/emitToast';
import { useTargetContext } from '../../common/context/TargetContext';
import { useOnValueChange } from './useOnValueChange';
import { useAstrometrics } from '../../common/context/AstrometricsContext';
import { useJobLog } from './useJobLog';
import { useJobEndWatcher } from './useJobEndWatcher';
import { announceFinishedStack } from '../utils/announceFinishedStack';

/** One light-frame group: its gain (null when not known), exposure in seconds and frame count. */
export type LightFrameRow = [number | null, number, number];

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
    const { invalidate } = useTargetContext();
    const { activeJobs } = useAstrometrics();
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

    // The light-frame summary is kept per target and reload counts (see useGroupedFrameStats), so
    // showing the display again or going back to a target already opened shows it at once with no
    // new call. `isLoadingFrames` is true on the very render the read becomes wanted (for example
    // when `shouldFetch` flips true once the shared target list confirms the target is local), so
    // the app boot readiness check in ImageProcessingDisplay.tsx never reads a false "done".
    const groupedFrames = useGroupedFrameStats(selectedTarget, shouldFetch);
    const isLoadingFrames = groupedFrames.isLoading;
    const lightFrames = useMemo<LightFrameRow[]>(
        () => (groupedFrames.groups ?? []).map((group) => [group.gain ?? null, group.exposureS, group.count]),
        [groupedFrames.groups]
    );

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
            // Only the restacked target's kept reads are read again.
            invalidate('frames', [selectedTarget]);
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
            invalidate('frames', [selectedTarget]);
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
        refreshFrames: () => invalidate('frames', [selectedTarget]),
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
