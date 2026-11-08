import React from 'react';
import { Button } from '../../common/components/Button';
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

    /** True when the app may not command the mount. Every button is then disabled. */
    disabled?: boolean;
    /** True when the app may not command alignment. Leave out to follow {@link disabled}. */
    alignmentDisabled?: boolean;
}

/**
 * TelescopeControl Component
 *
 * Provides an interface for tracking, parking, and alignment control.
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
    disabled = false,
    alignmentDisabled = disabled,
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
        if (action.startsWith('align') ? alignmentDisabled : disabled) return;
        setPendingAction(action);
        callback();
    };

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
                    onClick={() => handleAction('align_start', () => {
                        onStartAlignment?.();
                    })}
                    disabled={alignmentDisabled || isAligningActive}
                    aria-disabled={alignmentDisabled || isAligningActive}
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
        </div>
    );
};
