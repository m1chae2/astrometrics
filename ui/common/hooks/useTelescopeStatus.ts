import { useState, useEffect } from 'react';
import { AlignmentAttempt, AlignmentTargetSession, PolarAlignmentStatus } from '../types/backendTypes';
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
    /** The same attempts grouped by target, with tracking statistics from the library. */
    alignmentTargets: AlignmentTargetSession[];
    /** Whether an alignment run is currently in progress. */
    alignmentActive: boolean;
    /** Current polar alignment assistant status. */
    polarAlignment?: PolarAlignmentStatus | null;
    /** Current camera sensor temperature. */
    cameraTemperature?: string;
    /** Current camera exposure or idle status. */
    cameraStatus?: string;
    /** Catalog target currently being tracked/pointed at. */
    targetName?: string;
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
 * It only reads data. It never shows toasts (the status header hook does that).
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
        alignmentTargets: [],
        alignmentActive: false,
        polarAlignment: null,
    });
    const [telescopeConnection, setTelescopeConnection] = useState<boolean | null>(
        null
    );
    const [trackingStatus, setTrackingStatus] = useState<string>('Not Tracking');

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
            alignmentTargets: telescope.alignmentTargets ?? [],
            alignmentActive: telescope.alignmentActive ?? false,
            polarAlignment: telescope.polarAlignment ?? null,
            cameraTemperature: telescope.cameraTemperature ?? undefined,
            cameraStatus: telescope.cameraStatus ?? undefined,
            targetName: telescope.targetName ?? undefined,
        });

        // Parse Connection and Tracking status.
        const connRaw = telescope.connectionStatus;
        const trackRaw = telescope.trackingStatus;

        const connBool =
            connRaw === 'Connected' ? true : connRaw === 'Disconnected' ? false : null;

        // Transition toasts (tracking began or stopped, mount dropped) come only
        // from the always-mounted status header hook. This hook is used by displays
        // that can be paused, so a toast from here could be stale after a resume.
        if (typeof trackRaw === 'string' && trackRaw.trim().length) {
            setTrackingStatus(trackRaw);
        }
        setTelescopeConnection(connBool);
    }, [telescope]);

    return { telemetry, telescopeConnection, trackingStatus };
}
