/**
 * @file useIngestionManager.ts
 * @description Hook for managing the ingestion of images from telescope or remote folders.
 * Holds the form state and starts jobs. The running job itself (status
 * polling, logs and the finish step) lives in `IngestionJobsContext`, which is
 * always running, so a download keeps going and finishes while the display
 * that started it is hidden.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
    startIngestion,
    fetchRemoteFolderStats,
    fetchRemoteFiles,
} from '../services/imaging/ingestionService';
import { useRemoteStatusContext } from '../context/RemoteStatusContext';
import { IDLE_INGESTION_JOB, OBSERVATORY_INGESTION_CHANNEL, useIngestionJobs } from '../context/IngestionJobsContext';

export interface IngestionState {
    targetName: string;
    setTargetName: (name: string) => void;
    telescope: string;
    setTelescope: (name: string) => void;
    remoteFolder: string;
    setRemoteFolder: (folder: string) => void;
    remoteFolders: string[];
    remoteTargets: Set<string>; // Polled set of remote target names
    fileCount: number | null;
    remoteFiles: string[];
    selectedFiles: Set<string>;
    setSelectedFiles: (files: Set<string>) => void;
    isLoadingStats: boolean;
    jobId: string | null;
    /** The target the running (or last) download is for. It can differ from the form's target. */
    jobTargetName?: string;
    status: string;
    progress: string;
    logs: string[];
    scanningRemote: boolean;
    isIngestModalOpen: boolean;
    setIsIngestModalOpen: (open: boolean) => void;
    openIngestModal: () => void;
    closeIngestModal: () => void;
    startIngestionJob: () => Promise<void>;
    scanRemote: () => Promise<void>;
    refreshFiles: () => Promise<void>;
    resetState: () => void;
    isActive: boolean;
}

/**
 * Hook to manage ingestion state and operations for a target.
 * Handles the form state and starting a job. The job's status, progress and
 * logs come from `IngestionJobsContext`, which keeps following the job while
 * this display is hidden.
 *
 * @param initialTargetName The target the form starts on (it resets when this changes).
 * @param channel The name of the download this form owns (see `IngestionJobsContext`).
 */
export const useIngestionManager = (
    initialTargetName: string = '',
    channel: string = OBSERVATORY_INGESTION_CHANNEL
): IngestionState => {
    // Form State
    const [targetName, setTargetName] = useState(initialTargetName);
    const [telescope, setTelescope] = useState('Apertura 75Q');
    const [remoteFolder, setRemoteFolder] = useState('');
    const { remoteTargets, remoteFolders, refresh: scanRemote } = useRemoteStatusContext();
    const [fileCount, setFileCount] = useState<number | null>(null);
    const [remoteFiles, setRemoteFiles] = useState<string[]>([]);
    const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
    const [isLoadingStats, setIsLoadingStats] = useState(false);

    // Modal State
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);

    // Process State (kept by the app-wide provider)
    const { jobs, trackJob, resetJob, appendLog } = useIngestionJobs();
    const { jobId, status, progress, logs, targetName: jobTargetName } = jobs[channel] ?? IDLE_INGESTION_JOB;
    const [scanningRemote, setScanningRemote] = useState(false);

    // A download that is still running is never dropped, so the provider can
    // finish it even if the user picks another target meanwhile.
    const isJobActive = status === 'running' || status === 'queued';
    const isJobActiveRef = useRef(isJobActive);
    useEffect(() => {
        isJobActiveRef.current = isJobActive;
    });
    // The last target the form was reset for. A paused display runs its effects
    // again when it is shown; that must not reset a download that is on screen.
    const lastResetTargetRef = useRef<string | null>(null);

    // Reset state when target changes to prevent stale completion triggers and stale logs
    useEffect(() => {
        if (initialTargetName) {
            if (lastResetTargetRef.current === initialTargetName) return;
            lastResetTargetRef.current = initialTargetName;
            setTargetName(initialTargetName);
            if (!isJobActiveRef.current) resetJob(channel);
            setFileCount(null);
            setRemoteFolder('');
            setRemoteFiles([]);
            setSelectedFiles(new Set());

            // Check for existing active jobs
            import('../services/imaging/processingService').then(({ fetchJobs }) => {
                fetchJobs(initialTargetName, 'ingestion').then(jobs => {
                    const activeJob = jobs.find(j => j.status === 'started');
                    if (activeJob) {
                        trackJob(channel, activeJob.id, initialTargetName);
                    }
                });
            });
        }
    }, [initialTargetName, channel, resetJob, trackJob]);


    /**
     * Queries remote folder statistics and available files for the given folder or active target.
     *
     * @param folder The folder name or target name to scan.
     */
    const refreshFolderFiles = useCallback(async (folder: string) => {
        if (!folder) {
            setFileCount(null);
            setRemoteFiles([]);
            setSelectedFiles(new Set());
            return;
        }
        setIsLoadingStats(true);
        try {
            const [statsRes, filesRes] = await Promise.all([
                fetchRemoteFolderStats(folder),
                fetchRemoteFiles(folder)
            ]);
            setFileCount(statsRes.fileCount);
            setRemoteFiles(filesRes.files || []);
            // By default, select all files
            setSelectedFiles(new Set(filesRes.files || []));
        } catch (err) {
            console.error('Failed to get remote stats/files:', err);
            setFileCount(null);
            setRemoteFiles([]);
            setSelectedFiles(new Set());
        } finally {
            setIsLoadingStats(false);
        }
    }, []);

    /**
     * Refreshes the file list for the currently active target.
     */
    const refreshFiles = useCallback(async () => {
        if (targetName && targetName.length > 2) {
            await refreshFolderFiles(targetName);
        }
    }, [targetName, refreshFolderFiles]);

    // Debounce target name change for fetching stats
    useEffect(() => {
        const timer = setTimeout(() => {
            if (targetName && targetName.length > 2) {
                refreshFolderFiles(targetName);
            } else {
                setFileCount(null);
                setRemoteFolder('');
                setRemoteFiles([]);
                setSelectedFiles(new Set());
            }
        }, 500);

        return () => clearTimeout(timer);
    }, [targetName, refreshFolderFiles]);

    /**
     * Starts the ingestion job for the current target.
     */
    const startIngestionJob = async () => {
        if (!targetName) {
            appendLog(channel, "Target Name is required.");
            return;
        }

        try {
            const source = remoteFolder || targetName;
            const result = await startIngestion('remote', source, targetName, telescope, Array.from(selectedFiles));
            trackJob(channel, result.jobId, targetName);
        } catch (e) {
            appendLog(channel, `Failed to start: ${e}`);
        }
    };

    const resetState = () => {
        resetJob(channel);
        setFileCount(null);
        setRemoteFiles([]);
        setSelectedFiles(new Set());
    };

    const isActive = status === 'running' || status === 'queued';
    const openIngestModal = () => setIsIngestModalOpen(true);
    const closeIngestModal = useCallback(() => setIsIngestModalOpen(false), []);

    return {
        targetName, setTargetName,
        telescope, setTelescope,
        remoteFolder, setRemoteFolder,
        remoteFolders,
        remoteTargets,
        fileCount,
        remoteFiles,
        selectedFiles,
        setSelectedFiles,
        isLoadingStats,
        jobId, jobTargetName, status, progress, logs,
        scanningRemote,
        isIngestModalOpen,
        setIsIngestModalOpen,
        openIngestModal,
        closeIngestModal,
        startIngestionJob,
        scanRemote,
        refreshFiles,
        resetState,
        isActive
    };
};
