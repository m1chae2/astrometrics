/**
 * @file useAnalysisJob.ts
 * @description Hook for managing variability analysis jobs and results.
 * REQ: IMG-4: Data Analysis Workflow
 */
import { useState, useEffect, useRef } from 'react';
import { analyzeTarget, fetchAnalysisResults, streamJobLog } from '../../common/services/imaging/processingService';
import { reportError } from '../../common/utils/reportError';
import { AnalysisResult } from '../../common/types/backendTypes';
import { useAstrometrics } from '../../common/context/AstrometricsContext';

export interface AnalysisJobResult {
    isAnalyzing: boolean;
    analysisResults: AnalysisResult | null;
    startAnalysis: (imageFiles?: string[], filterType?: string) => Promise<void>;
}

export function useAnalysisJob(
    selectedTarget: string,
    shouldFetch: boolean = true,
    onLog?: (line: string) => void,
    onJobStarted?: (jobId: string) => void,
    onClearLogs?: () => void
): AnalysisJobResult {
    const { activeJobs } = useAstrometrics();
    const [analyzingTargetId, setAnalyzingTargetId] = useState<string | null>(null);
    const [analysisResults, setAnalysisResults] = useState<AnalysisResult | null>(null);
    const [activeAnalysisJobId, setActiveAnalysisJobId] = useState<string | null>(null);

    // useImageProcessing passes onLog/onJobStarted/onClearLogs as fresh inline
    // closures on every render, so callbacks here read the latest version via
    // ref rather than depending on them directly - otherwise effects/callbacks
    // that depend on these would re-run (and e.g. restart the log stream,
    // re-dumping its whole tail) on every unrelated re-render.
    const onLogRef = useRef(onLog);
    const onJobStartedRef = useRef(onJobStarted);
    const onClearLogsRef = useRef(onClearLogs);
    useEffect(() => {
        onLogRef.current = onLog;
        onJobStartedRef.current = onJobStarted;
        onClearLogsRef.current = onClearLogs;
    });

    // analyzingTargetId is read inside the activeJobs-driven effect below
    // without being a dependency of it, so that effect only re-runs when the
    // target or the shared active-jobs feed actually changes.
    const analyzingTargetIdRef = useRef(analyzingTargetId);
    useEffect(() => {
        analyzingTargetIdRef.current = analyzingTargetId;
    }, [analyzingTargetId]);

    const selectedTargetRef = useRef(selectedTarget);
    useEffect(() => {
        selectedTargetRef.current = selectedTarget;
    }, [selectedTarget]);

    // Whether the current analyzingTargetId's job has actually been observed
    // in activeJobs at least once. Guards against the window right after
    // startAnalysis()/an out-of-band start where the job hasn't reached the
    // shared feed yet -- without this, "not found in activeJobs" would be
    // indistinguishable from "already finished" and the effect below would
    // treat a job that hasn't even registered yet as already complete.
    const confirmedActiveJobIdRef = useRef<string | null>(null);

    // Clear state when target changes, then do a one-off check for a result
    // an analysis already completed before this hook mounted (e.g. switching
    // back to a previously-analyzed target). An in-flight job for the new
    // target, if any, is picked up by the activeJobs-driven effect below.
    useEffect(() => {
        setAnalysisResults(null);
        setAnalyzingTargetId(null);
        setActiveAnalysisJobId(null);
        confirmedActiveJobIdRef.current = null;

        if (!selectedTarget || !shouldFetch) return;
        let cancelled = false;
        fetchAnalysisResults(selectedTarget).then((results) => {
            if (cancelled || selectedTargetRef.current !== selectedTarget) return;
            if (results && (results.status === 'finished' || results.variableCandidates)) {
                setAnalysisResults(results);
            }
        }).catch(() => { /* Ignore */ });
        return () => { cancelled = true; };
    }, [selectedTarget, shouldFetch]);

    // Tracks analysis jobs via AstrometricsContext's shared active-jobs feed
    // instead of running its own polling loop: every pipeline job already
    // lands there (one poll for the whole app), so this only reaches out to
    // the backend itself once -- to fetch the final result -- when this
    // target's job transitions from active to no-longer-active. Also picks
    // up jobs started out-of-band (e.g. a standalone script), since the
    // shared feed isn't scoped to jobs this hook itself started.
    useEffect(() => {
        if (!selectedTarget || !shouldFetch) return;

        const activeJob = activeJobs.find((j) => j.targetId === selectedTarget && j.jobType === 'analysis');

        if (activeJob) {
            if (analyzingTargetIdRef.current !== selectedTarget) {
                setAnalyzingTargetId(selectedTarget);
            }
            confirmedActiveJobIdRef.current = activeJob.id;
            setActiveAnalysisJobId((prev) => (prev === activeJob.id ? prev : activeJob.id));
            return;
        }

        // Nothing active for this target. Only treat that as "just finished"
        // if we'd actually seen it active before -- otherwise this is the
        // window right after starting, before the shared feed has caught up.
        if (analyzingTargetIdRef.current !== selectedTarget || !confirmedActiveJobIdRef.current) return;
        confirmedActiveJobIdRef.current = null;

        let cancelled = false;
        fetchAnalysisResults(selectedTarget).then((results) => {
            if (cancelled || selectedTargetRef.current !== selectedTarget) return;
            if (results && (results.status === 'finished' || results.variableCandidates)) {
                setAnalysisResults(results);
                // Store this as the latest analyzed target for other views
                localStorage.setItem('latestAnalysisTargetId', selectedTarget);
                onLogRef.current?.(`[${new Date().toLocaleTimeString()}] Analysis complete for ${selectedTarget}.`);
            } else if (!results || results.status === 'failed' || results.status === 'error') {
                const msg = results?.error || results?.message || 'Analysis failed';
                onLogRef.current?.(`[${new Date().toLocaleTimeString()}] ERROR: ${msg}`);
            }
        }).catch(() => { /* Ignore */ }).finally(() => {
            if (cancelled) return;
            setAnalyzingTargetId(null);
            setActiveAnalysisJobId(null);
        });
        return () => { cancelled = true; };
    }, [selectedTarget, shouldFetch, activeJobs]);

    // Log Streaming - mirrors the mechanism useStackingJob uses for its own
    // job log panel: poll the DB-backed job log tail for the active job.
    useEffect(() => {
        let cancelStream: (() => void) | undefined;
        let cancelled = false;
        if (analyzingTargetId && activeAnalysisJobId) {
            streamJobLog(activeAnalysisJobId, (line) => onLogRef.current?.(line)).then((cancel) => {
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
    }, [analyzingTargetId, activeAnalysisJobId]);

    const startAnalysis = async (imageFiles?: string[], filterType?: string) => {
        if (!selectedTarget) return;
        const targetToAnalyze = selectedTarget;
        setAnalyzingTargetId(targetToAnalyze);
        setAnalysisResults(null);
        onClearLogsRef.current?.();

        onLogRef.current?.(`[${new Date().toLocaleTimeString()}] Analysis started for ${targetToAnalyze}...`);

        try {
            const result = await analyzeTarget(targetToAnalyze, imageFiles, filterType);
            const jobId: string | undefined = result ? (result as any).jobId : undefined;
            if (jobId) {
                setActiveAnalysisJobId(jobId);
                onJobStartedRef.current?.(jobId);
            }
            // The activeJobs-driven effect above picks up completion from here.
        } catch (err) {
            setAnalyzingTargetId(null);
            setActiveAnalysisJobId(null);
            onLogRef.current?.(`[${new Date().toLocaleTimeString()}] Analysis failed to start.`);
            reportError(err, 'startAnalysis');
        }
    };

    return {
        isAnalyzing: analyzingTargetId === selectedTarget,
        analysisResults,
        startAnalysis
    };
}
