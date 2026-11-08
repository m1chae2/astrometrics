import { useState, useEffect, useRef } from 'react';
import { useAstrometrics } from '../../common/context/AstrometricsContext';
import { emitToast } from '../../common/utils/emitToast';
import { isBackendRestarting } from '../../common/services/backendApi';
import { isAppReady } from '../../common/utils/appBootReadiness';
import { isTrackingStatus } from '../../common/utils/isTrackingStatus';
import { normalizeAppMode } from '../../common/utils/normalizeAppMode';
import { useGuidingRms } from './useGuidingRms';

/** How long after the camera last exposed a capture still counts as in progress (covers the gap between frames). */
const CAPTURE_GAP_MS = 10000;

export interface StatusTelemetry {
    altitude: string;
    azimuth: string;
    temperature: string;
    humidity: string;
    ra: string;
    dec: string;
    cameraTemperature?: string;
    cameraStatus?: string;
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
            return normalizeAppMode(window.localStorage.getItem('appMode') || 'Image Viewer');
        } catch {
            return 'Image Viewer';
        }
    });

    // Keeps the header's mode label in sync with mode switches that don't go
    // through chooseMode() below -- e.g. a remote "navigate" socket action, or
    // a cross-view hand-off like Planetarium's "Open in Astronomy Manager" --
    // both of which dispatch this event directly rather than calling chooseMode.
    useEffect(() => {
        const handleModeChange = (event: Event) => {
            const nextMode = (event as CustomEvent<string>).detail;
            if (nextMode) setSelectedMode(normalizeAppMode(nextMode));
        };
        window.addEventListener('astrometrics:modeChange', handleModeChange);
        return () => window.removeEventListener('astrometrics:modeChange', handleModeChange);
    }, []);

    // Refs for transition detection
    const prevTelescopeConnection = useRef<boolean | null>(null);
    const prevTelescopeTracking = useRef<string | null>(null);

    useEffect(() => {
        // REQ: HDR-5.1 - Round all telemetry to whole numbers
        const normalize = (v?: string) => (!v || /^unknown$/i.test(v)) ? '-' : v;
        const stripDecimals = (v: string): string => {
            if (!v || v === '-') return v;

            // Handle coordinate strings: [+-]XX° YY' ZZ.ZZ"
            const coordRegex = /^([+-]?\d+°\s*)(\d+)['′]\s*(\d+(?:\.\d+)?)(["″])$/;
            const coordMatch = v.match(coordRegex);
            if (coordMatch) {
                const [_, degPrefix, minutes, seconds, suffix] = coordMatch;
                const roundedSec = Math.round(parseFloat(seconds));
                const padSec = String(roundedSec).padStart(2, '0');
                const padMin = String(parseInt(minutes, 10)).padStart(2, '0');
                return `${degPrefix}${padMin}' ${padSec}${suffix}`;
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
            humidity: stripDecimals(normalize(telescope.humidity).replace(/\s*%$/, '')),
            cameraTemperature: telescope.cameraTemperature ?? undefined,
            cameraStatus: telescope.cameraStatus ?? undefined,
        });

        // Status Logic
        const connBool = telescope.connectionStatus === 'Connected';
        const trackStatus = telescope.trackingStatus || 'Not Tracking';

        // Tracking Transitions
        const prevTrack = prevTelescopeTracking.current;
        const newIsTracking = isTrackingStatus(trackStatus);
        const prevIsTracking = isTrackingStatus(prevTrack);

        // Tracking starting or stopping is a status the chip shows; no toast.
        prevTelescopeTracking.current = trackStatus;

        // Connection Transitions
        const prevConn = prevTelescopeConnection.current;
        // An offline telescope is a status, and the status chip shows it. Only a
        // mount that drops while it was tracking (a night is under way) needs the
        // user, so only that is a warning, and never during start-up.
        if (prevConn !== null && prevConn !== connBool && isAppReady()) {
            // Coming back online is a status the chip shows; no toast.
            // A backend restart the user asked for (a source switch) drops the mount on purpose; that is no warning.
            if (!connBool && prevIsTracking && !isBackendRestarting()) {
                emitToast('Telescope disconnected while tracking', 'warning', 'telescope', { notify: true });
            }
        }
        prevTelescopeConnection.current = connBool;

    }, [telescope]);

    // Guiding lost: guiding was running and its samples stopped while a capture was
    // in progress. The app has no Start/Stop Guiding buttons, so it cannot tell a stop
    // the user wanted (for example in Ekos) from a lost guide star. A stop between
    // captures is therefore quiet. A capture is in progress while the camera is
    // exposing or reading out, and for a few seconds after, which covers the gap
    // between frames. This hook is always mounted, so the alert fires whichever
    // display is on screen.
    const guidingRms = useGuidingRms(telescope.guidingHistory);
    const guidingActive = guidingRms !== null;
    const prevGuidingActive = useRef<boolean | null>(null);
    const lastCaptureMs = useRef<number | null>(null);
    const cameraStatusText = telescope.cameraStatus ?? '';
    useEffect(() => {
        if (/expos|download|reading/i.test(cameraStatusText)) lastCaptureMs.current = Date.now();
    }, [cameraStatusText]);
    useEffect(() => {
        const wasActive = prevGuidingActive.current;
        prevGuidingActive.current = guidingActive;
        if (guidingActive) return;
        const capturing =
            lastCaptureMs.current !== null && Date.now() - lastCaptureMs.current <= CAPTURE_GAP_MS;
        if (wasActive === true && isAppReady() && capturing) {
            emitToast('Guiding lost. Check the guide star and the guider.', 'warning', 'guiding', { notify: true });
        }
    }, [guidingActive]);

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
