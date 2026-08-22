/**
 * Service for managing frame ingestion, remote scanning, and library reindexing via the JSON-RPC backend api.
 */

import { callBackend } from '../backendApi';

export interface IngestResponse {
    jobId: string;
}

export interface IngestStatusResponse {
    status: string;
    progress: string;
    logs: string[];
}

export interface RemoteScanResponse {
    folders: string[];
}

/**
 * Initiates frame ingestion from a local or remote directory.
 */
export const startIngestion = async (
    type: 'local' | 'remote',
    sourcePath: string,
    targetName?: string,
    telescope?: string,
    selectedFiles?: string[]
): Promise<IngestResponse> => {
    try {
        const result = await callBackend('ingestion:start', {
            type,
            sourcePath,
            targetName,
            telescope,
            selectedFiles
        });
        return result || { jobId: '' };
    } catch {
        throw new Error('Failed to start ingestion');
    }
};

/**
 * Fetches status of an ongoing ingestion job.
 */
export const fetchIngestStatus = async (jobId: string): Promise<IngestStatusResponse> => {
    try {
        const result = await callBackend('ingestion:status', { job_id: jobId });
        return result || { status: 'failed', progress: '0%', logs: [] };
    } catch {
        throw new Error('Failed to fetch status');
    }
};

/**
 * Scans the remote telescope for available target folders.
 */
export const scanRemoteTargets = async (): Promise<RemoteScanResponse> => {
    try {
        const result = await callBackend('ingestion:scan', {});
        return result || { folders: [] };
    } catch {
        throw new Error('Failed to scan remote targets');
    }
};

/**
 * Fetches file count stats for a remote target folder on the telescope.
 */
export const fetchRemoteFolderStats = async (
    folder: string
): Promise<{ fileCount: number; resolvedFolder?: string }> => {
    try {
        const result = await callBackend('ingestion:stats', { folder });
        return result || { fileCount: 0 };
    } catch {
        return { fileCount: 0 };
    }
};

/**
 * Fetches the list of individual files for a remote target folder.
 */
export const fetchRemoteFiles = async (
    folder: string
): Promise<{ files: string[]; resolvedFolder?: string }> => {
    try {
        const result = await callBackend('ingestion:list_files', { folder });
        return result || { files: [] };
    } catch {
        return { files: [] };
    }
};
