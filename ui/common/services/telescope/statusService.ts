/**
 * @fileoverview Service for querying telescope connection and telemetry status.
 * Communicates via JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';
import { reportError } from '../../utils/reportError';
import { TelescopeStatus } from '../../types/backendTypes';

const defaultStatus: TelescopeStatus = {
    ra: 'Unknown',
    dec: 'Unknown',
    altitude: 'Unknown',
    azimuth: 'Unknown',
    temperature: 'Unknown',
    humidity: 'Unknown',
    connectionStatus: 'Disconnected',
    trackingStatus: 'Not Tracking',
    focuserPosition: 0,
    filter: '',
    guidingHistory: [],
    alignmentAttempts: [],
};

function mapTelescopeStatus(data: Record<string, unknown>): TelescopeStatus {
    return data as unknown as TelescopeStatus;
}

/**
 * Fetches the current telescope telemetry and connection status.
 * @return Mapped TelescopeStatus payload.
 */
export async function fetchTelescopeStatus(): Promise<TelescopeStatus> {
    try {
        const data = await callBackend("telescope:status", {});
        if (data) {
            return mapTelescopeStatus(data as Record<string, unknown>);
        }
        return defaultStatus;
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return defaultStatus;
    }
}
