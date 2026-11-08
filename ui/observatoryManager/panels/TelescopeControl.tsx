import React from 'react';
import { Button } from '../../common/components/Button';
// Styles handled by panels.css

export interface TelescopeControlProps {
    isTracking: boolean;
    isParked: boolean;
    /** Turns tracking on or off. Resolve to false (or reject) when it did not happen, so the button stops showing "pending". */
    onSetTracking: (enabled: boolean) => Promise<unknown> | void;
    /** Parks the mount. Resolve to false (or reject) when it did not happen. */
    onPark: () => Promise<unknown> | void;
    /** Unparks the mount. Resolve to false (or reject) when it did not happen. */
    onUnpark: () => Promise<unknown> | void;

    // Alignment control
    /** Starts alignment on the selected target. Resolve to false (or reject) when it did not start. */
    onStartAlignment?: () => Promise<unknown> | void;
    /** Stops alignment. Resolve to false (or reject) when nothing was stopped. */
    onStopAlignment?: () => Promise<unknown> | void;
    isAligningActive?: boolean;
    /** Why Start Alignment cannot run even though commands are on (no target picked). Null or empty when it can. */
    startAlignmentBlockedReason?: string | null;

    // Capture control
    /** Starts the open-ended capture. Return false when it did not start, so the button stops showing "pending". */
    onStartCapture?: () => Promise<boolean> | boolean | void;
    /** Stops the capture. Return false when nothing was stopped. */
    onStopCapture?: () => Promise<boolean> | boolean | void;
    /** True while a capture job runs. */
    isCapturing?: boolean;
    /** Why Start Capture cannot run even though commands are on (no target to capture). Null or empty when it can. */
    startCaptureBlockedReason?: string | null;
    /** True when the app may not command the camera. Leave out to follow {@link disabled}. */
    captureDisabled?: boolean;

    /** True when the app may not command the mount. Every button is then disabled. */
    disabled?: boolean;
    /** True when the app may not command alignment. Leave out to follow {@link disabled}. */
    alignmentDisabled?: boolean;
}

/** How long a button shows "pending" when the telescope state never changes, in milliseconds. */
export const PENDING_ACTION_TIMEOUT_MS = 15000;

/**
 * TelescopeControl Component
 *
 * Provides an interface for tracking, parking, capture and alignment control.
 * Displays buttons with active and pending states based on telescope telemetry.
 */
export const TelescopeControl: React.FC<TelescopeControlProps> = ({
    isTracking,
    isParked,
    onSetTracking,
    onPark,
    onUnpark,
    onStartAlignment,
    onStopAlignment,
    isAligningActive = false,
    startAlignmentBlockedReason = null,
    disabled = false,
    alignmentDisabled = disabled,
    onStartCapture,
    onStopCapture,
    isCapturing = false,
    startCaptureBlockedReason = null,
    captureDisabled = disabled,
}) => {
    // Local state to track which action is pending
    const [pendingAction, setPendingAction] = React.useState<string | null>(null);

    // Effect to clear pending state when props update to match expectation
    React.useEffect(() => {
        if (pendingAction === 'tracking_on' && isTracking) setPendingAction(null);
        if (pendingAction === 'tracking_off' && !isTracking) setPendingAction(null);
        if (pendingAction === 'park' && isParked) setPendingAction(null);
        if (pendingAction === 'unpark' && !isParked) setPendingAction(null);
        if (pendingAction === 'align_start' && isAligningActive) setPendingAction(null);
        if (pendingAction === 'align_stop' && !isAligningActive) setPendingAction(null);
        if (pendingAction === 'capture_start' && isCapturing) setPendingAction(null);
        if (pendingAction === 'capture_stop' && !isCapturing) setPendingAction(null);
    }, [isTracking, isParked, isAligningActive, isCapturing, pendingAction]);

    // Safety net: a command whose state never changes must not leave its button spinning.
    React.useEffect(() => {
        if (pendingAction === null) return undefined;
        const timer = setTimeout(() => setPendingAction(null), PENDING_ACTION_TIMEOUT_MS);
        return () => clearTimeout(timer);
    }, [pendingAction]);

    /**
     * Runs a command. The button shows "pending" until the telescope state follows. It stops at once
     * if the command reports that it did nothing (resolves to false) or fails (rejects), and after a
     * time limit if the state never changes.
     * @param action Which button was pressed.
     * @param callback The command to run.
     */
    const handleAction = (action: string, callback: () => Promise<unknown> | void) => {
        if (action.startsWith('align') ? alignmentDisabled : disabled) return;
        setPendingAction(action);
        const clearThisAction = () => setPendingAction((current) => (current === action ? null : current));
        void (async () => {
            try {
                const outcome = await callback();
                if (outcome === false) clearThisAction();
            } catch {
                clearThisAction();
            }
        })();
    };

    /**
     * Runs a capture command. The button shows "pending" until the capture state follows; if the command
     * reports that it did nothing (it returns false), the pending state ends at once.
     * @param action Which capture button was pressed.
     * @param callback The command to run.
     */
    const handleCaptureAction = (action: 'capture_start' | 'capture_stop', callback?: () => Promise<boolean> | boolean | void) => {
        if (captureDisabled) return;
        setPendingAction(action);
        const clearThisAction = () => setPendingAction((current) => (current === action ? null : current));
        void Promise.resolve(callback?.()).then((accepted) => {
            if (accepted === false) clearThisAction();
        }, clearThisAction);
    };

    const startCaptureUnavailable = captureDisabled || isCapturing || Boolean(startCaptureBlockedReason);
    const startCaptureTitle = isCapturing
        ? 'A capture is running.'
        : startCaptureBlockedReason ?? 'Take 60 s luminance light frames until you press Stop Capture.';

    const startAlignmentUnavailable = alignmentDisabled || isAligningActive || Boolean(startAlignmentBlockedReason);
    const startAlignmentTitle = isAligningActive
        ? 'Alignment is running.'
        : startAlignmentBlockedReason ?? 'Center the mount on the selected target by plate solving.';

    return (
        <div id="telescope-status-controls" className="telescope-control">
            {/* Alignment Control Row */}
            <div className="telescope-control__row">
                <Button
                    id="btn-start-alignment"
                    control
                    variant="primary"
                    active={isAligningActive}
                    pending={pendingAction === 'align_start'}
                    onClick={() => handleAction('align_start', () => onStartAlignment?.())}
                    disabled={startAlignmentUnavailable}
                    aria-disabled={startAlignmentUnavailable}
                    title={startAlignmentTitle}
                >
                    Start Alignment
                </Button>
                <Button
                    id="btn-stop-alignment"
                    control
                    variant="danger"
                    active={!isAligningActive}
                    pending={pendingAction === 'align_stop'}
                    onClick={() => handleAction('align_stop', () => onStopAlignment?.())}
                    disabled={alignmentDisabled || !isAligningActive}
                    aria-disabled={alignmentDisabled || !isAligningActive}
                >
                    Stop Alignment
                </Button>
            </div>

            {/* Tracking Control Row */}
            <div className="telescope-control__row">
                <Button
                    id="btn-tracking-on"
                    disabled={disabled}
                    aria-disabled={disabled}
                    control
                    variant="primary"
                    active={isTracking}
                    pending={pendingAction === 'tracking_on'}
                    onClick={() => handleAction('tracking_on', () => onSetTracking(true))}
                >
                    Tracking ON
                </Button>
                <Button
                    id="btn-tracking-off"
                    disabled={disabled}
                    aria-disabled={disabled}
                    control
                    variant="primary"
                    active={!isTracking}
                    pending={pendingAction === 'tracking_off'}
                    onClick={() => handleAction('tracking_off', () => onSetTracking(false))}
                >
                    Tracking OFF
                </Button>
            </div>

            {/* Park Control Row */}
            <div className="telescope-control__row">
                <Button
                    id="btn-telescope-park"
                    disabled={disabled}
                    aria-disabled={disabled}
                    control
                    variant="primary"
                    active={isParked}
                    pending={pendingAction === 'park'}
                    onClick={() => handleAction('park', onPark)}
                    data-parked={isParked.toString()}
                >
                    PARK
                </Button>
                <Button
                    id="btn-telescope-unpark"
                    disabled={disabled}
                    aria-disabled={disabled}
                    control
                    variant="primary"
                    active={!isParked}
                    pending={pendingAction === 'unpark'}
                    onClick={() => handleAction('unpark', onUnpark)}
                >
                    UNPARK
                </Button>
            </div>

            {/* Capture Control Row */}
            <div className="telescope-control__row">
                <Button
                    id="btn-start-capture"
                    control
                    variant="primary"
                    active={isCapturing}
                    pending={pendingAction === 'capture_start'}
                    onClick={() => handleCaptureAction('capture_start', onStartCapture)}
                    disabled={startCaptureUnavailable}
                    aria-disabled={startCaptureUnavailable}
                    title={startCaptureTitle}
                >
                    Start Capture
                </Button>
                <Button
                    id="btn-stop-capture"
                    control
                    variant="danger"
                    active={!isCapturing}
                    pending={pendingAction === 'capture_stop'}
                    onClick={() => handleCaptureAction('capture_stop', onStopCapture)}
                    disabled={captureDisabled || !isCapturing}
                    aria-disabled={captureDisabled || !isCapturing}
                    title={isCapturing ? 'Stop the capture. Frames already taken are kept.' : 'No capture is running.'}
                >
                    Stop Capture
                </Button>
            </div>
        </div>
    );
};
