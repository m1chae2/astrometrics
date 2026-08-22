/**
 * @file useIngestionManager.ts
 * @description Hook for managing the ingestion of images from telescope or remote folders.
 * Orchestrates job creation, status polling, and log management.
 */
import { useState, useEffect, useRef } from 'react';
import {
    startIngestion,
    fetchIngestStatus,
    scanRemoteTargets,
    fetchRemoteFolderStats,
    fetchRemoteFiles,
    IngestStatusResponse
} from '../services/imaging/ingestionService';
import { useRemoteStatusContext } from '../context/RemoteStatusContext';
import { IngestionJobStatus } from '../services/ingestionService';
import { ProcessingJob } from '../types/backendTypes';
import { emit as emitEvent } from '../utils/eventBus';

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
    resetState: () => void;
    isActive: boolean;
}

/**
 * Hook to manage ingestion state and operations for a target.
 * Handles form state, job status polling, and log persistence.
 */
export const useIngestionManager = (initialTargetName: string = ''): IngestionState => {
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

    // Process State
    const [jobId, setJobId] = useState<string | null>(null);
    const [status, setStatus] = useState<string>('idle');
    const [progress, setProgress] = useState<string>('');
    const [logs, setLogs] = useState<string[]>([]);
    const [scanningRemote, setScanningRemote] = useState(false);

    // Reset state when target changes to prevent stale completion triggers and stale logs
    useEffect(() => {
        if (initialTargetName) {
            setTargetName(initialTargetName);
            setStatus('idle');
            setJobId(null);
            setProgress('');
            setLogs([]);
            setFileCount(null);
            setRemoteFolder('');
            setRemoteFiles([]);
            setSelectedFiles(new Set());

            // Check for existing active jobs
            import('../services/imaging/processingService').then(({ fetchJobs }) => {
                fetchJobs(initialTargetName, 'ingestion').then(jobs => {
                    const activeJob = jobs.find(j => j.status === 'started');
                    if (activeJob) {
                        setJobId(activeJob.id);
                        setStatus('running');
                    }
                });
            });
        }
    }, [initialTargetName]);


    // Debounce target name change for fetching stats
    useEffect(() => {
        const timer = setTimeout(() => {
            const checkFolderStats = async (folder: string) => {
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
            };
            if (targetName && targetName.length > 2) {
                checkFolderStats(targetName);
            } else {
                setFileCount(null);
                setRemoteFolder('');
                setRemoteFiles([]);
                setSelectedFiles(new Set());
            }
        }, 500);

        return () => clearTimeout(timer);
    }, [targetName]);

    /**
     * Starts the ingestion job for the current target.
     */
    const startIngestionJob = async () => {
        if (!targetName) {
            setLogs(prev => [...prev, "Target Name is required."]);
            return;
        }

        try {
            const source = remoteFolder || targetName;
            const result = await startIngestion('remote', source, targetName, telescope, Array.from(selectedFiles));
            setJobId(result.jobId);
            setStatus('running');
            setLogs([]);
        } catch (e) {
            setLogs(prev => [...prev, `Failed to start: ${e}`]);
        }
    };

    // Poll status
    useEffect(() => {
        if (!jobId || status === 'completed' || status === 'failed') return;

        const interval = setInterval(async () => {
            try {
                const statusResponse = await fetchIngestStatus(jobId);
                if (statusResponse) {
                    setStatus(statusResponse.status);
                    setProgress(statusResponse.progress);
                    setLogs(statusResponse.logs || []);
                }
            } catch (e) {
                console.error("Polling failed", e);
            }
        }, 1000);
        return () => clearInterval(interval);
    }, [jobId, status]);

    const resetState = () => {
        setJobId(null);
        setStatus('idle');
        setLogs([]);
        setProgress('');
        setFileCount(null);
        setRemoteFiles([]);
        setSelectedFiles(new Set());
    };

    const isActive = status === 'running' || status === 'queued';
    const openIngestModal = () => setIsIngestModalOpen(true);
    const closeIngestModal = () => setIsIngestModalOpen(false);

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
        jobId, status, progress, logs,
        scanningRemote,
        isIngestModalOpen,
        setIsIngestModalOpen,
        openIngestModal,
        closeIngestModal,
        startIngestionJob,
        scanRemote,
        resetState,
        isActive
    };
};
