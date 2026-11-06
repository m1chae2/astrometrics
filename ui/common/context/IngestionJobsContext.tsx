/**
 * @file IngestionJobsContext.tsx
 * @description Keeps frame downloads from the telescope running and finishing
 * no matter which display is on screen.
 *
 * A download is a backend job. Someone has to ask the backend how it is doing
 * every second, and when it ends someone has to make sure the target exists,
 * refresh the lists and select the target. That work used to happen inside the
 * Observatory Manager. A hidden display can be paused (React `Activity`), so
 * the work now lives here, in a provider that is always running. The
 * Observatory Manager and the calibration library only read the job's state
 * (through `useIngestionManager`) and start or reset it.
 *
 * Each place that can start a download owns one "channel" (a short name), so
 * two places never mix up their jobs.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { fetchIngestStatus } from '../services/imaging/ingestionService';
import { createTarget, fetchTargetObject } from '../services/targetService';
import { emit as emitEvent } from '../utils/eventBus';
import { emitToast } from '../utils/emitToast';
import { useTargetContext } from './TargetContext';

/** The channel the Observatory Manager's Download Frames window uses. */
export const OBSERVATORY_INGESTION_CHANNEL = 'observatory';
/** The channel the calibration library's ingest window uses. */
export const CALIBRATION_INGESTION_CHANNEL = 'calibration';

/** How often a running download is checked, in milliseconds. */
const INGESTION_POLL_INTERVAL_MS = 1000;

/** What the app knows about one download. */
export interface IngestionJob {
    /** The backend job id, or null when nothing was started. */
    jobId: string | null;
    /** The target the frames are downloaded for. */
    targetName: string;
    /** `idle`, `queued`, `running`, `completed` or `failed`. */
    status: string;
    /** The backend's short progress text. */
    progress: string;
    /** The backend's log lines (plus any the app added). */
    logs: string[];
}

/** The state of a channel that has no download. */
export const IDLE_INGESTION_JOB: IngestionJob = Object.freeze({
    jobId: null,
    targetName: '',
    status: 'idle',
    progress: '',
    logs: [],
}) as IngestionJob;

interface IngestionJobsContextValue {
    /** The download of each channel (a channel with none is absent). */
    jobs: Readonly<Record<string, IngestionJob>>;
    /** Starts following a backend job on a channel. */
    trackJob: (channel: string, jobId: string, targetName: string, status?: string) => void;
    /** Forgets the download of a channel. */
    resetJob: (channel: string) => void;
    /** Adds one line to a channel's log (for errors the app itself found). */
    appendLog: (channel: string, line: string) => void;
}

const IngestionJobsContext = createContext<IngestionJobsContextValue | undefined>(undefined);

/** Tells whether a status means the job is over. */
const isFinished = (status: string): boolean => status === 'completed' || status === 'failed';

/**
 * Provider that follows every frame download and finishes it.
 * Place it inside `TargetProvider` and `QueryClientProvider`, outside the displays.
 *
 * @param props.children The app below it.
 */
export const IngestionJobsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [jobs, setJobs] = useState<Record<string, IngestionJob>>({});
    const jobsRef = useRef(jobs);
    useEffect(() => {
        jobsRef.current = jobs;
    });
    const queryClient = useQueryClient();
    const { forceReload, setSelectedTarget, setPendingTarget, selectedTarget } = useTargetContext();
    // The selection now, and the selection when each job started, so a finished
    // download only moves the selection if the user has not moved on.
    const selectedTargetRef = useRef<string>(selectedTarget ?? '');
    useEffect(() => {
        selectedTargetRef.current = selectedTarget ?? '';
    });
    const selectionAtStartRef = useRef<Map<string, string>>(new Map());
    /** Job ids that already failed or logged a polling problem, so each is reported once. */
    const failedJobIds = useRef<Set<string>>(new Set());
    const pollingWarnedJobIds = useRef<Set<string>>(new Set());

    // Keep the newest target actions in a ref so the finish step below always
    // uses the current ones without restarting the polling.
    const targetActionsRef = useRef({ forceReload, setSelectedTarget, setPendingTarget });
    useEffect(() => {
        targetActionsRef.current = { forceReload, setSelectedTarget, setPendingTarget };
    });
    /** Job ids whose finish step already ran, so each runs once. */
    const finishedJobIds = useRef<Set<string>>(new Set());

    const trackJob = useCallback((channel: string, jobId: string, targetName: string, status = 'running') => {
        selectionAtStartRef.current.set(jobId, selectedTargetRef.current);
        setJobs((previous) => ({
            ...previous,
            [channel]: { jobId, targetName, status, progress: '', logs: [] },
        }));
    }, []);

    const resetJob = useCallback((channel: string) => {
        setJobs((previous) => {
            if (!(channel in previous)) return previous;
            const next = { ...previous };
            delete next[channel];
            return next;
        });
    }, []);

    const appendLog = useCallback((channel: string, line: string) => {
        setJobs((previous) => {
            const current = previous[channel] ?? IDLE_INGESTION_JOB;
            return { ...previous, [channel]: { ...current, logs: [...current.logs, line] } };
        });
    }, []);

    /**
     * Runs when a download ends well. Frames for the calibration library only
     * need its counts and health read again. Frames for a target make sure the
     * target has a record, refresh the lists and select the target (only if the
     * selection is still what it was when the download started; otherwise a toast
     * offers to open the target).
     *
     * @param channel The channel that finished.
     * @param targetName The target the frames were downloaded for.
     * @param jobId The backend job id.
     */
    const finishJob = useCallback(
        async (channel: string, targetName: string, jobId: string) => {
            if (channel === CALIBRATION_INGESTION_CHANNEL) {
                void queryClient.invalidateQueries({ queryKey: ['calibrationLibrary'] });
                void queryClient.invalidateQueries({ queryKey: ['calibrationHealth'] });
                return;
            }
            try {
                const existing = await fetchTargetObject(targetName);
                if (!existing) await createTarget(targetName);
            } catch {
                try {
                    await createTarget(targetName);
                } catch (error) {
                    emitEvent('terminal:log', `[Ingest] Failed to ensure target exists: ${error}`);
                }
            }
            const actions = targetActionsRef.current;
            actions.forceReload();
            emitEvent('targetsUpdated');
            const selectionAtStart = selectionAtStartRef.current.get(jobId) ?? '';
            selectionAtStartRef.current.delete(jobId);
            if (selectedTargetRef.current === selectionAtStart || selectedTargetRef.current === targetName) {
                actions.setSelectedTarget(targetName);
                actions.setPendingTarget(targetName);
            } else {
                // The user moved on to another target: do not pull them back.
                emitToast(`Frames for ${targetName} are downloaded`, 'info', 'download', {
                    action: {
                        label: 'Open',
                        run: () => {
                            targetActionsRef.current.setSelectedTarget(targetName);
                            targetActionsRef.current.setPendingTarget(targetName);
                        },
                    },
                });
            }
        },
        [queryClient]
    );

    // Check every running download once a second, whichever display is shown.
    const activeSignature = Object.entries(jobs)
        .filter(([, job]) => job.jobId && !isFinished(job.status))
        .map(([channel, job]) => `${channel}:${job.jobId}`)
        .join('|');
    useEffect(() => {
        if (!activeSignature) return undefined;
        const interval = setInterval(async () => {
            const active = Object.entries(jobsRef.current).filter(
                ([, job]) => job.jobId && !isFinished(job.status)
            );
            for (const [channel, job] of active) {
                try {
                    const statusResponse = await fetchIngestStatus(job.jobId as string);
                    if (!statusResponse) continue;
                    setJobs((previous) => {
                        const current = previous[channel];
                        // The channel was reset or restarted while we waited.
                        if (!current || current.jobId !== job.jobId) return previous;
                        return {
                            ...previous,
                            [channel]: {
                                ...current,
                                status: statusResponse.status,
                                progress: statusResponse.progress,
                                logs: statusResponse.logs || [],
                            },
                        };
                    });
                    if (statusResponse.status === 'failed' && !failedJobIds.current.has(job.jobId as string)) {
                        failedJobIds.current.add(job.jobId as string);
                        // A download that fails during a night needs the user.
                        emitToast(`Frame download for ${job.targetName} failed`, 'error', 'download', { notify: true });
                    }
                    if (statusResponse.status === 'completed' && !finishedJobIds.current.has(job.jobId as string)) {
                        finishedJobIds.current.add(job.jobId as string);
                        void finishJob(channel, job.targetName, job.jobId as string);
                    }
                } catch (error) {
                    // A short network blip is normal; say so once per job, as a warning.
                    if (!pollingWarnedJobIds.current.has(job.jobId as string)) {
                        pollingWarnedJobIds.current.add(job.jobId as string);
                        console.warn('Download status check failed; will keep trying', error);
                    }
                }
            }
        }, INGESTION_POLL_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [activeSignature, finishJob]);

    const value = useMemo(
        () => ({ jobs, trackJob, resetJob, appendLog }),
        [jobs, trackJob, resetJob, appendLog]
    );
    return <IngestionJobsContext.Provider value={value}>{children}</IngestionJobsContext.Provider>;
};

/**
 * Reads and controls the downloads.
 *
 * @return The jobs and the functions to start, reset and log them.
 */
export const useIngestionJobs = (): IngestionJobsContextValue => {
    const context = useContext(IngestionJobsContext);
    if (!context) {
        throw new Error('useIngestionJobs must be used within an IngestionJobsProvider');
    }
    return context;
};
