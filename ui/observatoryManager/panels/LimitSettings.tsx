import React, { useState } from 'react';
// CSS handled by panels.css (common styles)

export interface LimitSettingsProps {
    minAltitude?: number;
    maxAltitude?: number;
    maxHourAngle?: number;
    meridianFlipHourAngle?: number;
    onApplyLimits: (
        enabled: boolean,
        minimumAltitude: number,
        maximumAltitude: number,
        hourAngleEnabled: boolean,
        maximumHourAngle: number,
        flipHourAngle: number
    ) => void;
}

/**
 * LimitSettings Component
 *
 * Provides controls for setting safety limits for the telescope, including
 * Provides controls for setting safety limits for the telescope, including
 * altitude limits and hour angle limits for meridian flips.
 * REQ: OBS-8: Safety Limits
 */
export const LimitSettings: React.FC<LimitSettingsProps> = ({
    minAltitude = 0,
    maxAltitude = 90,
    maxHourAngle = 2,
    meridianFlipHourAngle = 1,
    onApplyLimits,
}) => {
    const [enableAltitudeLimits, setEnableAltitudeLimits] = React.useState(false);
    const [minimumAltitude, setMinimumAltitude] = React.useState(String(minAltitude));
    const [maximumAltitude, setMaximumAltitude] = React.useState(String(maxAltitude));
    const [enableHourAngleLimits, setEnableHourAngleLimits] = React.useState(false);
    const [maximumHourAngle, setMaximumHourAngle] = React.useState(String(maxHourAngle));
    const [flipHourAngle, setFlipHourAngle] = React.useState(String(meridianFlipHourAngle));

    return (
        <div id="safety-limits-group" className="controls__group">
            {/* Altitude Limits Section */}
            <div className="controls__group">
                <div className="checkbox-container">
                    <input
                        id="checkbox-enable-alt-limits"
                        type="checkbox"
                        // REQ: OBS-8.1: The display SHALL allow enabling and disabling of altitude limits.
                        checked={enableAltitudeLimits}
                        onChange={(e) => setEnableAltitudeLimits(e.target.checked)}
                        className="checkbox"
                    />
                    <label htmlFor="checkbox-enable-alt-limits">
                        Enable Altitude Limits
                    </label>
                </div>

                <div id="row-limit-min-alt" className="parameter">
                    <label id="label-limit-min-alt" className="parameter__label">Min. Altitude:</label>
                    <input
                        id="input-limit-min-alt"
                        type="number"
                        step="0.01"
                        className="input input--short"
                        // REQ: OBS-8.2: The display SHALL allow configuration of Minimum Altitude Limit.
                        value={minimumAltitude}
                        onChange={(e) => setMinimumAltitude(e.target.value)}
                        disabled={!enableAltitudeLimits}
                    />
                </div>

                <div id="row-limit-max-alt" className="parameter">
                    <label id="label-limit-max-alt" className="parameter__label">Max. Altitude:</label>
                    <input
                        id="input-limit-max-alt"
                        type="number"
                        step="0.01"
                        className="input input--short"
                        value={maximumAltitude}
                        onChange={(e) => setMaximumAltitude(e.target.value)}
                        disabled={!enableAltitudeLimits}
                    />
                </div>
            </div>

            {/* Hour Angle Limits Section */}
            <div className="controls__group limit-settings__group--margined">
                <div className="checkbox-container">
                    <input
                        id="checkbox-enable-ha-limits"
                        type="checkbox"
                        // REQ: OBS-8.3: The display SHALL allow enabling and disabling of hour angle limits.
                        checked={enableHourAngleLimits}
                        onChange={(e) => setEnableHourAngleLimits(e.target.checked)}
                        className="checkbox"
                    />
                    <label htmlFor="checkbox-enable-ha-limits">
                        Enable Hour Angle Limits
                    </label>
                </div>

                <div id="row-limit-max-ha" className="parameter">
                    <label id="label-limit-max-ha" className="parameter__label">Max. Hour Angle:</label>
                    <input
                        id="input-limit-max-ha"
                        type="number"
                        step="0.01"
                        className="input input--short"
                        // REQ: OBS-8.4: The display SHALL allow configuration of Maximum Hour Angle Limit.
                        value={maximumHourAngle}
                        onChange={(e) => setMaximumHourAngle(e.target.value)}
                        disabled={!enableHourAngleLimits}
                    />
                </div>

                <div id="row-meridian-flip-ha" className="parameter">
                    <label id="label-meridian-flip-ha" className="parameter__label">Flip if HA &gt;:</label>
                    <div className="controls__group-row limit-settings__group-row--auto">
                        <input
                            id="input-meridian-flip-ha"
                            type="number"
                            step="0.01"
                            className="input input--short"
                            // REQ: OBS-8.5: The display SHALL allow configuration of Meridian Flip Hour Angle.
                            value={flipHourAngle}
                            onChange={(e) => setFlipHourAngle(e.target.value)}
                        />
                        <span className="limit-settings__unit">deg</span>
                    </div>
                </div>
            </div>
        </div>
    );
};
