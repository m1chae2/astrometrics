/**
 * @fileoverview Centralized global React Context and State Reducer for Astrometrics.
 * Consolidates real-time WebSockets events and aggregates telemetry states.
 * Aligns with the Google TypeScript Style Guide.
 */

import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, ReactNode } from 'react';
import { socketClient } from '../utils/socketClient';
import { SystemPulse, SystemHealth, TelescopePulse, ProcessingJobPulse } from '../types/backendTypes';
import { getSystemConfig } from '../services/systemService';

export interface AstrometricsContextValue {
    /** Telecope pulse status */
    telescope: TelescopePulse;
    /** Background processing jobs queue pulse */
    processing: ProcessingJobPulse[];
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
                if (payload.telescope) {
                    setTelescope(payload.telescope);
                }
                if (payload.processing) {
                    setProcessing(payload.processing);
                }
                if (payload.health) {
                    setHealth(payload.health);
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

    const value: AstrometricsContextValue = useMemo(() => ({
        telescope,
        processing,
        health,
        connected,
        config,
        refetchConfig,
    }), [telescope, processing, health, connected, config, refetchConfig]);

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
