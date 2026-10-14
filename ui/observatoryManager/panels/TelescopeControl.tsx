import React from 'react';
import '../../common/styles/button.css';
// Styles handled by panels.css

export interface TelescopeControlProps {
    isTracking: boolean;
    isParked: boolean;
    onSetTracking: (enabled: boolean) => void;
    onPark: () => void;
    onUnpark: () => void;

    // Alignment control
    onStartAlignment?: () => void;
    onStopAlignment?: () => void;
    isAligningActive?: boolean;
}

/**
 * TelescopeControl Component
 *
 * Provides an interface for tracking, parking, and alignment control.
 * Provides an interface for tracking, parking, and alignment control.
 * Displays buttons with active and pending states based on telescope telemetry.
 * REQ: OBS-4.1: The display SHALL provide controls to Park and Unpark the mount.
 * REQ: OBS-4.2: The display SHALL indicate current park status (Parked/Unparked).
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
    }, [isTracking, isParked, isAligningActive, pendingAction]);

    const handleAction = (action: string, callback: () => void) => {
        setPendingAction(action);
        callback();
    };

    // Helper for common button classes
    const getBtnClass = (isActive: boolean, action: string, color?: 'success' | 'danger') => {
        return `btn btn--control ${color ? `btn--${color}` : ''} ${isActive ? 'btn--active' : ''} ${pendingAction === action ? 'btn--pending' : ''}`;
    };

    return (
        <div id="telescope-status-controls" className="telescope-control">
            {/* Alignment Control Row */}
            <div className="telescope-control__row">
                <button
                    id="btn-start-alignment"
                    className={getBtnClass(isAligningActive, 'align_start', 'success')}
                    onClick={() => handleAction('align_start', () => {
                        onStartAlignment?.();
                    })}
                    disabled={isAligningActive}
                    type="button"
                >
                    Start Alignment
                </button>
                <button
                    id="btn-stop-alignment"
                    className={getBtnClass(!isAligningActive, 'align_stop', 'danger')}
                    onClick={() => handleAction('align_stop', () => onStopAlignment?.())}
                    disabled={!isAligningActive}
                    type="button"
                >
                    Stop Alignment
                </button>
            </div>

            {/* Tracking Control Row */}
            <div className="telescope-control__row">
                <button
                    id="btn-tracking-on"
                    className={getBtnClass(isTracking, 'tracking_on', 'success')}
                    onClick={() => handleAction('tracking_on', () => onSetTracking(true))}
                    type="button"
                >
                    Tracking ON
                </button>
                <button
                    id="btn-tracking-off"
                    className={getBtnClass(!isTracking, 'tracking_off', 'success')}
                    onClick={() => handleAction('tracking_off', () => onSetTracking(false))}
                    type="button"
                >
                    Tracking OFF
                </button>
            </div>

            {/* REQ: OBS-4: Tracking & Parking Control */}
            {/* Park Control Row */}
            <div className="telescope-control__row">
                <button
                    id="btn-telescope-park"
                    className={getBtnClass(isParked, 'park', 'success')}
                    onClick={() => handleAction('park', onPark)}
                    type="button"
                    data-parked={isParked.toString()}
                >
                    PARK
                </button>
                <button
                    id="btn-telescope-unpark"
                    className={getBtnClass(!isParked, 'unpark', 'success')}
                    onClick={() => handleAction('unpark', onUnpark)}
                    type="button"
                >
                    UNPARK
                </button>
            </div>
        </div>
    );
};
