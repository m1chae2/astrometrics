/**
 * @file useAnalysisJob.ts
 * @description Hook for managing variability analysis jobs and results.
 * REQ: IMG-4: Data Analysis Workflow
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
    analyzeTarget,
    fetchAnalysisResults,
    streamJobLog,
} from '../../common/services/imagingService';
import { reportError } from '../../common/utils/reportError';
import { AnalysisResult } from '../../common/types/backendTypes';

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
    const [analyzingTargetId, setAnalyzingTargetId] = useState<string | null>(null);
    const [analysisResults, setAnalysisResults] = useState<AnalysisResult | null>(null);
    const [activeAnalysisJobId, setActiveAnalysisJobId] = useState<string | null>(null);
    const analysisMonitorRef = useRef<number | null>(null);

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

    // analyzingTargetId is read inside the watchdog poll below without being
    // a dependency of it, so the interval isn't torn down and recreated on
    // every start/stop transition (monitorAnalysis already owns that
    // lifecycle once a job is picked up).
    const analyzingTargetIdRef = useRef(analyzingTargetId);
    useEffect(() => {
        analyzingTargetIdRef.current = analyzingTargetId;
    }, [analyzingTargetId]);

    // Clear state when target changes
    useEffect(() => {
        setAnalysisResults(null);
        setAnalyzingTargetId(null);
        setActiveAnalysisJobId(null);
    }, [selectedTarget]);

    const monitorAnalysis = useCallback((targetId: string) => {
        if (analysisMonitorRef.current) window.clearInterval(analysisMonitorRef.current);
        analysisMonitorRef.current = window.setInterval(async () => {
            try {
                const results = await fetchAnalysisResults(targetId);
                if (results && (results.status === 'finished' || results.variableCandidates)) {
                    if (selectedTarget === targetId) {
                        setAnalysisResults(results);
                        // Store this as the latest analyzed target for other views
                        localStorage.setItem('latestAnalysisTargetId', targetId);
                    }
                    onLogRef.current?.(`[${new Date().toLocaleTimeString()}] Analysis complete for ${targetId}.`);
                    setAnalyzingTargetId(null);
                    setActiveAnalysisJobId(null);
                    if (analysisMonitorRef.current) {
                        window.clearInterval(analysisMonitorRef.current);
                        analysisMonitorRef.current = null;
                    }
                } else if (!results || results.status === 'failed' || results.status === 'error') {
                    const msg = results?.error || results?.message || 'Analysis failed';
                    onLogRef.current?.(`[${new Date().toLocaleTimeString()}] ERROR: ${msg}`);
                    setAnalyzingTargetId(null);
                    setActiveAnalysisJobId(null);
                    if (analysisMonitorRef.current) {
                        window.clearInterval(analysisMonitorRef.current);
                        analysisMonitorRef.current = null;
                    }
                }
            } catch {
                setAnalyzingTargetId(null);
                setActiveAnalysisJobId(null);
                if (analysisMonitorRef.current) {
                    window.clearInterval(analysisMonitorRef.current);
                    analysisMonitorRef.current = null;
                }
            }
        }, 2000);
    }, [selectedTarget]);

    // Check analysis state on target switch, then keep polling: analysis
    // jobs aren't only started from this hook's own startAnalysis() (e.g. a
    // standalone script calling analyze_target() directly), so without a
    // recurring check here a job started elsewhere would never be picked up
    // -- once monitorAnalysis takes over for an in-flight job it clears this
    // interval's work by short-circuiting on analyzingTargetIdRef.
    useEffect(() => {
        if (!selectedTarget || !shouldFetch) {
            setAnalysisResults(null);
            return;
        }

        let mounted = true;

        const checkForJob = () => {
            if (!mounted || analyzingTargetIdRef.current) return;
            fetchAnalysisResults(selectedTarget).then((results) => {
                if (!mounted) return;
                if (results?.status === 'started') {
                    setAnalyzingTargetId(selectedTarget);
                    if (results.jobId) setActiveAnalysisJobId(results.jobId);
                    monitorAnalysis(selectedTarget);
                } else if (results && (results.status === 'finished' || results.variableCandidates)) {
                    setAnalysisResults(results);
                }
            }).catch(() => { });
        };

        checkForJob();
        const timer = window.setInterval(checkForJob, 4000);

        return () => {
            mounted = false;
            window.clearInterval(timer);
        };
    }, [selectedTarget, shouldFetch, monitorAnalysis]);

    useEffect(() => {
        return () => {
            if (analysisMonitorRef.current) window.clearInterval(analysisMonitorRef.current);
        };
    }, []);

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
            monitorAnalysis(targetToAnalyze);
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
