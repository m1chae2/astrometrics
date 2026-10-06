/**
 * Service for managing frame ingestion, remote scanning, and library reindexing via the JSON-RPC backend api.
 */

import { callBackend } from '../backendApi';
import { BackendError } from '../backendError';

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

/** The status shown for a job the backend cannot find, so polling stops. */
const FAILED_INGEST_STATUS: IngestStatusResponse = { status: 'failed', progress: '0%', logs: [] };

/**
 * Fetches status of an ongoing ingestion job.
 *
 * The backend raises `not_found` for a job it does not know (for example
 * after a restart) and `configuration` when it has no job table. Both mean
 * the job will never finish, so they are shown as a failed job, which stops
 * the polling loop. The call is silent because it runs every second.
 *
 * @param jobId The ingestion job to look up.
 * @return The job's status, progress, and log lines.
 */
export const fetchIngestStatus = async (jobId: string): Promise<IngestStatusResponse> => {
    try {
        const result = await callBackend('ingestion:status', { job_id: jobId }, { silent: true });
        return result || FAILED_INGEST_STATUS;
    } catch (error) {
        if (error instanceof BackendError && (error.code === 'not_found' || error.code === 'configuration')) {
            return FAILED_INGEST_STATUS;
        }
        throw new Error('Failed to fetch status');
    }
};

/**
 * Scans the remote telescope for available target folders.
 */
export const scanRemoteTargets = async (): Promise<RemoteScanResponse> => {
    try {
        const result = await callBackend('ingestion:scan', {}, { silent: true });
        return result || { folders: [] };
    } catch {
        return { folders: [] };
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
