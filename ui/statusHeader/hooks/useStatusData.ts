import { useState, useEffect, useRef } from 'react';
import { useAstrometrics } from '../../common/context/AstrometricsContext';
import { emitToast } from '../../common/utils/emitToast';

export interface StatusTelemetry {
    altitude: string;
    azimuth: string;
    temperature: string;
    humidity: string;
    ra: string;
    dec: string;
}

export const useStatusData = () => {
    const { telescope, connected: wsConnected } = useAstrometrics();

    // State
    const [telemetry, setTelemetry] = useState<StatusTelemetry>({
        altitude: '-',
        azimuth: '-',
        temperature: '-',
        humidity: '-',
        ra: '-',
        dec: '-'
    });

    const [selectedMode, setSelectedMode] = useState<string>(() => {
        try {
            return window.localStorage.getItem('appMode') || 'Image Viewer';
        } catch {
            return 'Image Viewer';
        }
    });

    // Refs for transition detection
    const prevTelescopeConnection = useRef<boolean | null>(null);
    const prevTelescopeTracking = useRef<string | null>(null);

    useEffect(() => {
        // REQ: HDR-5.1 - Round all telemetry to whole numbers
        const normalize = (v?: string) => (!v || /^unknown$/i.test(v)) ? '-' : v;
        const stripDecimals = (v: string): string => {
            if (!v || v === '-') return v;

            // Handle coordinate strings: [+-]XX° YY' ZZ.ZZ"
            const coordRegex = /^([+-]?\d+°\s*\d+['′]\s*)(\d+(?:\.\d+)?)(["″])$/;
            const coordMatch = v.match(coordRegex);
            if (coordMatch) {
                const [_, prefix, seconds, suffix] = coordMatch;
                const rounded = Math.round(parseFloat(seconds));
                return `${prefix}${rounded}${suffix}`;
            }

            // Handle plain numeric strings (for temp/humidity)
            const num = parseFloat(v);
            if (!isNaN(num) && /^[+-]?\d+(\.\d+)?$/.test(v.trim())) {
                return Math.round(num).toString();
            }

            return v;
        };

        setTelemetry({
            ra: stripDecimals(normalize(telescope.ra)),
            dec: stripDecimals(normalize(telescope.dec)),
            altitude: stripDecimals(normalize(telescope.altitude)),
            azimuth: stripDecimals(normalize(telescope.azimuth)),
            temperature: stripDecimals(normalize(telescope.temperature).replace(/\s*°C$/, '')),
            humidity: stripDecimals(normalize(telescope.humidity).replace(/\s*%$/, ''))
        });

        // Status Logic
        const connBool = telescope.connectionStatus === 'Connected';
        const trackStatus = telescope.trackingStatus || 'Not Tracking';

        // Tracking Transitions
        const prevTrack = prevTelescopeTracking.current;
        const newIsTracking = /\btracking\b/i.test(trackStatus);
        const prevIsTracking = typeof prevTrack === 'string' && /\btracking\b/i.test(prevTrack);

        if (prevTrack !== null && prevTrack !== trackStatus) {
            if (!prevIsTracking && newIsTracking) {
                emitToast('Telescope began tracking', 'success', 'telescope');
            } else if (prevIsTracking && !newIsTracking) {
                emitToast('Telescope stopped tracking', 'info', 'telescope');
            }
        }
        prevTelescopeTracking.current = trackStatus;

        // Connection Transitions
        const prevConn = prevTelescopeConnection.current;
        if (prevConn !== null && prevConn !== connBool) {
            if (connBool) emitToast('Telescope connected', 'success', 'telescope');
            else emitToast('Telescope disconnected', 'error', 'telescope');
        }
        prevTelescopeConnection.current = connBool;

    }, [telescope]);

    const chooseMode = (mode: string): void => {
        setSelectedMode(mode);
        try {
            window.localStorage.setItem('appMode', mode);
        } catch { /* ignore */ }
        window.dispatchEvent(
            new CustomEvent('astrometrics:modeChange', { detail: mode })
        );
    };

    return {
        telemetry,
        connected: wsConnected,
        trackingStatus: telescope.trackingStatus || 'Not Tracking',
        telescopeConnection: telescope.connectionStatus === 'Connected',
        selectedMode,
        chooseMode
    };
};
