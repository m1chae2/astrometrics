import { useState, useRef, useEffect } from 'react';
import { emitToast } from '../utils/emitToast';
import { AlignmentAttempt } from '../types/backendTypes';
import { useAstrometrics } from '../context/AstrometricsContext';

/** Represents the telemetry data returned from the telescope. */
export interface TelescopeTelemetry {
    /** Current altitude in degrees. */
    altitude: string;
    /** Current azimuth in degrees. */
    azimuth: string;
    /** Ambient temperature value. */
    temperature: string;
    /** Ambient humidity percentage. */
    humidity: string;
    /** Current Right Ascension coordinate. */
    ra: string;
    /** Current Declination coordinate. */
    dec: string;
    /** Current focuser position. */
    focuserPosition: number;
    /** Active filter. */
    filter: string;
    /** Recent guiding history samples. */
    guidingHistory: any[];
    /** List of alignment attempts. */
    alignmentAttempts: AlignmentAttempt[];
    /** Whether an alignment run is currently in progress. */
    alignmentActive: boolean;
}

/** Result of the useTelescopeStatus hook. */
export interface UseTelescopeStatusResult {
    /** Current telescope telemetry values. */
    telemetry: TelescopeTelemetry;
    /** Connection status: true if connected, false if disconnected, null if unknown. */
    telescopeConnection: boolean | null;
    /** Human-readable tracking status string. */
    trackingStatus: string;
}

/**
 * Custom hook to consume live telescope status from the centralized state context.
 */
export function useTelescopeStatus(): UseTelescopeStatusResult {
    const { telescope } = useAstrometrics();

    const [telemetry, setTelemetry] = useState<TelescopeTelemetry>({
        altitude: '-',
        azimuth: '-',
        temperature: '-',
        humidity: '-',
        ra: '-',
        dec: '-',
        focuserPosition: 0,
        filter: '',
        guidingHistory: [],
        alignmentAttempts: [],
        alignmentActive: false,
    });
    const [telescopeConnection, setTelescopeConnection] = useState<boolean | null>(
        null
    );
    const [trackingStatus, setTrackingStatus] = useState<string>('Not Tracking');

    const prevTelescopeConnection = useRef<boolean | null>(null);
    const prevTelescopeTracking = useRef<string | null>(null);

    useEffect(() => {
        if (!telescope) return;

        const alt = telescope.altitude;
        const az = telescope.azimuth;
        const temp = telescope.temperature;
        const hum = telescope.humidity;
        const ra = telescope.ra ?? '-';
        const dec = telescope.dec ?? '-';
        const focPos = telescope.focuserPosition ?? 0;
        const activeFilter = telescope.filter ?? 'L';
        const guideHist = telescope.guidingHistory ?? [];

        /** Normalizes telemetry values for display. */
        const normalize = (v?: string): string => {
            if (!v) return '-';
            const t = String(v).trim();
            if (!t || /^unknown$/i.test(t)) return '-';
            return t;
        };

        setTelemetry({
            altitude: normalize(alt),
            azimuth: normalize(az),
            temperature: normalize(temp).replace(/\s*°C$/, ''),
            humidity: normalize(hum).replace(/\s*%$/, ''),
            ra: normalize(ra),
            dec: normalize(dec),
            focuserPosition: Number(focPos),
            filter: activeFilter,
            guidingHistory: guideHist,
            alignmentAttempts: telescope.alignmentAttempts ?? [],
            alignmentActive: telescope.alignmentActive ?? false,
        });

        // Parse Connection and Tracking status.
        const connRaw = telescope.connectionStatus;
        const trackRaw = telescope.trackingStatus;

        const connBool =
            connRaw === 'Connected' ? true : connRaw === 'Disconnected' ? false : null;

        // Handle tracking state transitions.
        if (typeof trackRaw === 'string' && trackRaw.trim().length) {
            const prevTrack = prevTelescopeTracking.current;
            const newTrack = trackRaw;
            const prevIsTracking =
                typeof prevTrack === 'string' && /\btracking\b/i.test(prevTrack);
            const newIsTracking = /\btracking\b/i.test(newTrack);

            if (prevTrack !== null && prevTrack !== newTrack) {
                if (!prevIsTracking && newIsTracking) {
                    emitToast('Telescope began tracking', 'success', 'telescope');
                } else if (prevIsTracking && !newIsTracking) {
                    emitToast('Telescope stopped tracking', 'info', 'telescope');
                }
            }
            prevTelescopeTracking.current = newTrack;
            setTrackingStatus(newTrack);
        }

        // Handle connection state transitions.
        const prevConn = prevTelescopeConnection.current;
        if (prevConn === null) {
            prevTelescopeConnection.current = connBool;
            setTelescopeConnection(connBool);
        } else {
            if (prevConn === true && connBool === false) {
                emitToast('Telescope disconnected', 'error', 'telescope');
            } else if (prevConn === false && connBool === true) {
                emitToast('Telescope connected', 'success', 'telescope');
            }
            prevTelescopeConnection.current = connBool;
            setTelescopeConnection(connBool);
        }
    }, [telescope]);

    return { telemetry, telescopeConnection, trackingStatus };
}
