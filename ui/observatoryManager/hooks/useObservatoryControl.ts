/**
 * @file useObservatoryControl.ts
 * @description The settings state of the Observatory Manager: the safety
 * limits and the guiding exposure and gain. The device commands (slew, park,
 * focus, guide and so on) moved to Observation Execution
 * (`ui/observationExecution/hooks/useEquipmentCommands.ts`).
 */

import { useState } from 'react';
import { emitToast } from '../../common/utils/emitToast';

/**
 * Holds the settings the Observatory Manager shows.
 * @return The limits, the guiding settings and their change handlers.
 */
export const useObservatoryControl = () => {
    // State for LimitSettings
    const [limitsConfig, setLimitsConfig] = useState({
        altitudeEnabled: false,
        minimumAltitude: 0,
        maximumAltitude: 90,
        hourAngleEnabled: false,
        maximumHourAngle: 2,
        flipHourAngle: 1,
    });

    // State for Guiding
    const [guidingExposure, setGuidingExposure] = useState<number>(1.0);
    const [guidingGain, setGuidingGain] = useState<number>(100);

    const handleApplyLimits = (
        enabled: boolean,
        minimumAltitude: number,
        maximumAltitude: number,
        hourAngleEnabled: boolean,
        maximumHourAngle: number,
        flipHourAngle: number
    ) => {
        setLimitsConfig({
            altitudeEnabled: enabled,
            minimumAltitude,
            maximumAltitude,
            hourAngleEnabled,
            maximumHourAngle,
            flipHourAngle,
        });
        emitToast(`Limits updated: Alt ${enabled ? 'ON' : 'OFF'} (${minimumAltitude}°-${maximumAltitude}°), HA ${hourAngleEnabled ? 'ON' : 'OFF'} (max ${maximumHourAngle}h), Flip HA: ${flipHourAngle}°`, 'success');
    };

    return {
        limitsConfig,
        guidingExposure,
        setGuidingExposure,
        guidingGain,
        setGuidingGain,
        handlers: {
            onApplyLimits: handleApplyLimits,
        }
    };
};
