/**
 * @fileoverview Centralized global React Context and State Reducer for Astrometrics.
 * Consolidates real-time WebSockets events and aggregates telemetry states.
 * Aligns with the Google TypeScript Style Guide.
 */

import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import { socketClient } from '../utils/socketClient';
import { SystemPulse, SystemHealth, TelescopePulse, ProcessingJobPulse, ProcessingJob } from '../types/backendTypes';
import { getSystemConfig } from '../services/systemService';
import { fetchAllProcesses } from '../services/imaging/processingService';

/** Job statuses that still count as "in progress" (mirrors JobService.get_active_jobs on the backend). */
const ACTIVE_JOB_STATUSES = new Set(['started', 'running']);

/** How often to poll for job progress while at least one job is active. */
const JOB_PROGRESS_POLL_MS = 2000;

/**
 * Deep-equality check for plain JSON-shaped telemetry payloads (arrays and
 * objects of primitives). The backend's telemetry loop rebroadcasts the full
 * system state every tick whether or not anything changed, so this lets
 * `setState` bail out and keep the previous reference instead of forcing
 * every consumer of this context to re-render on an unchanged pulse.
 */
function isEqualPulse(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    try {
        return JSON.stringify(a) === JSON.stringify(b);
    } catch {
        return false;
    }
}

export interface AstrometricsContextValue {
    /** Telecope pulse status */
    telescope: TelescopePulse;
    /** Background processing jobs queue pulse */
    processing: ProcessingJobPulse[];
    /**
     * Full detail (including progress) for jobs currently active anywhere in
     * the app, shared so feature hooks (stacking, analysis, ...) can read
     * live job progress without each running their own polling loop against
     * the same backend data.
     */
    activeJobs: ProcessingJob[];
    /** Diagnostic system health metrics */
    health: SystemHealth;
    /** Real-time WebSocket connection status */
    connected: boolean;
    /** System configuration data */
    config: Record<string, Record<string, any>>;
    /** Refetches the system configuration from the backend */
    refetchConfig: () => Promise<void>;
}

const defaultTelescopeState: TelescopePulse = {
    ra: "00 00 00",
    dec: "+00 00 00",
    altitude: "0.0",
    azimuth: "0.0",
    trackingStatus: "Idle",
    connectionStatus: "Disconnected",
    temperature: "0.0",
    humidity: "0.0",
    filter: "None",
    focuserPosition: 0
};

const defaultHealthState: SystemHealth = {
    resources: {
        system_ram_usage_percent: 0.0,
        vram_status: "Unknown"
    },
    indi: {
        status: "Disconnected"
    }
};

const AstrometricsContext = createContext<AstrometricsContextValue | undefined>(undefined);

interface AstrometricsProviderProps {
    children: ReactNode;
}

export const AstrometricsProvider: React.FC<AstrometricsProviderProps> = ({ children }) => {
    const [telescope, setTelescope] = useState<TelescopePulse>(defaultTelescopeState);
    const [processing, setProcessing] = useState<ProcessingJobPulse[]>([]);
    const [activeJobs, setActiveJobs] = useState<ProcessingJob[]>([]);
    const [health, setHealth] = useState<SystemHealth>(defaultHealthState);
    const [connected, setConnected] = useState<boolean>(false);
    const [config, setConfig] = useState<Record<string, Record<string, any>>>({});

    const refetchConfig = useCallback(async () => {
        try {
            const data = await getSystemConfig();
            setConfig(data);
        } catch (err) {
            console.error("Failed to load config", err);
        }
    }, []);

    useEffect(() => {
        refetchConfig();

        const handleConfigChange = () => {
            refetchConfig();
        };

        window.addEventListener('astrometrics:configChange', handleConfigChange);

        // Handle WebSocket connection states
        const onConnected = () => setConnected(true);
        const onDisconnected = () => setConnected(false);

        socketClient.on('connected', onConnected);
        socketClient.on('disconnected', onDisconnected);

        // Ensure active connection is run
        socketClient.connect();

        // Listen for unified backend telemetry updates
        const onAction = (action: string, payload: any) => {
            if (action === 'system_state_update' && payload) {
                // The backend rebroadcasts the whole state every tick regardless of
                // whether anything changed; bail out per-field instead of calling
                // setState unconditionally, so unrelated tabs/views don't re-render
                // every couple of seconds while the app sits idle.
                if (payload.telescope) {
                    setTelescope((prev) => (isEqualPulse(prev, payload.telescope) ? prev : payload.telescope));
                }
                if (payload.processing) {
                    setProcessing((prev) => (isEqualPulse(prev, payload.processing) ? prev : payload.processing));
                }
                if (payload.health) {
                    setHealth((prev) => (isEqualPulse(prev, payload.health) ? prev : payload.health));
                }
            }
        };

        socketClient.on('action', onAction);

        return () => {
            window.removeEventListener('astrometrics:configChange', handleConfigChange);
            socketClient.off('connected', onConnected);
            socketClient.off('disconnected', onDisconnected);
            socketClient.off('action', onAction);
        };
        // refetchConfig is useCallback([]), so it never changes identity and
        // this effect still runs exactly once; listing it satisfies the
        // exhaustive-deps rule without altering subscription behaviour.
    }, [refetchConfig]);

    // Every pipeline (stacking, astrometry, photometry, spectroscopy, asteroid
    // detection, ingestion, ...) registers through the same job service, whose
    // pulses land here. This is the ONLY poll for full job detail (including
    // progress) anywhere in the app: it drives the OS taskbar/dock progress
    // indicator directly, and feature hooks read `activeJobs` from context
    // instead of each running their own competing poll against the same data.
    const activeJobIds = useMemo(
        () => processing
            .filter((job) => ACTIVE_JOB_STATUSES.has(job.status))
            .map((job) => job.job_id)
            .sort()
            .join(','),
        [processing]
    );

    useEffect(() => {
        if (!activeJobIds) {
            setActiveJobs((prev) => (prev.length === 0 ? prev : []));
            window.astrometrics?.app?.setProgress(0, 'none');
            return;
        }

        let cancelled = false;

        const updateActiveJobs = async () => {
            const jobs = await fetchAllProcesses();
            if (cancelled) return;

            setActiveJobs((prev) => (isEqualPulse(prev, jobs) ? prev : jobs));
            if (jobs.length === 0) return;

            const fractions = jobs
                .filter((job) => typeof job.progressTotal === 'number' && job.progressTotal > 0)
                .map((job) => Math.min(1, Math.max(0, (job.progressCurrent ?? 0) / (job.progressTotal as number))));

            if (fractions.length === 0) {
                // Jobs are active but haven't reported a progress fraction yet.
                window.astrometrics?.app?.setProgress(1, 'indeterminate');
                return;
            }

            const averageFraction = fractions.reduce((sum, fraction) => sum + fraction, 0) / fractions.length;
            window.astrometrics?.app?.setProgress(averageFraction, 'normal');
        };

        updateActiveJobs();
        const interval = setInterval(updateActiveJobs, JOB_PROGRESS_POLL_MS);

        return () => {
            cancelled = true;
            clearInterval(interval);
        };
    }, [activeJobIds]);

    const value: AstrometricsContextValue = useMemo(() => ({
        telescope,
        processing,
        activeJobs,
        health,
        connected,
        config,
        refetchConfig,
    }), [telescope, processing, activeJobs, health, connected, config, refetchConfig]);

    return (
        <AstrometricsContext.Provider value={value}>
            {children}
        </AstrometricsContext.Provider>
    );
};

/**
 * Hook to consume the centralized Astrometrics telemetry and connection state.
 * @throws Error if used outside AstrometricsProvider.
 */
export const useAstrometrics = (): AstrometricsContextValue => {
    const context = useContext(AstrometricsContext);
    if (context === undefined) {
        throw new Error('useAstrometrics must be used within an AstrometricsProvider');
    }
    return context;
};
