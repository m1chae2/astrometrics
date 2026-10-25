import React from 'react';
import {
    ControlContainer,
    ControlColumn,
    SectionLabel,
    ParameterRow,
    VerticalDivider
} from '../../common/components/ControlLayoutComponents';

export interface TrackingSettingsProps {
    // Alignment settings
    accuracy?: number;
    settleTime?: number;
    alignmentExposure?: number;
    onAccuracyChange?: (value: number) => void;
    onSettleTimeChange?: (value: number) => void;
    onAlignmentExposureChange?: (value: number) => void;

    // Guide settings
    guidingExposure?: number;
    guidingGain?: number;
    onGuidingExposureChange?: (value: number) => void;
    onGuidingGainChange?: (value: number) => void;

    /** When true every input is unavailable (the app is not allowed to command the device). */
    disabled?: boolean;
    /** The id of the visible text that says why the inputs are unavailable. */
    disabledReasonId?: string;
}

/**
 * TrackingSettings Component
 *
 * Displays and allows editing of telescope tracking configurations, including
 * Displays and allows editing of telescope tracking configurations, including
 * alignment parameters and guiding camera settings.
 * REQ: OBS-7.2: The display SHALL allow configuration of guide camera exposure and gain.
 */
export const TrackingSettings: React.FC<TrackingSettingsProps> = ({
    accuracy = 30,
    settleTime = 1500,
    alignmentExposure = 1,
    onAccuracyChange,
    onSettleTimeChange,
    onAlignmentExposureChange,
    guidingExposure = 1.0,
    guidingGain = 100,
    onGuidingExposureChange,
    onGuidingGainChange,
    disabled = false,
    disabledReasonId,
}) => {
    const unavailable = {
        disabled,
        'aria-disabled': disabled || undefined,
        'aria-describedby': disabled ? disabledReasonId : undefined,
    };
    return (
        <ControlContainer className="tracking-settings">
            {/* Left: Alignment Settings */}
            <ControlColumn>
                <SectionLabel>Alignment</SectionLabel>
                <ParameterRow label="Accuracy:">
                    <input
                        {...unavailable}
                        id="input-tracking-accuracy"
                        name="accuracy"
                        type="number"
                        value={accuracy}
                        onChange={(e) => !disabled && onAccuracyChange?.(parseInt(e.target.value, 10))}
                        className="input input--short"
                        aria-label="Alignment Accuracy"
                    />
                </ParameterRow>
                <ParameterRow label="Settle:">
                    <input
                        {...unavailable}
                        id="input-tracking-settle"
                        name="settleTime"
                        type="number"
                        value={settleTime}
                        onChange={(e) => !disabled && onSettleTimeChange?.(parseInt(e.target.value, 10))}
                        className="input input--short"
                        aria-label="Settle Time"
                    />
                </ParameterRow>
                <ParameterRow label="Exposure (s):">
                    <input
                        {...unavailable}
                        id="input-tracking-expose"
                        name="alignmentExposure"
                        type="number"
                        value={alignmentExposure}
                        onChange={(e) => !disabled && onAlignmentExposureChange?.(parseFloat(e.target.value))}
                        className="input input--short"
                        step="0.1"
                        aria-label="Alignment Exposure Seconds"
                    />
                </ParameterRow>
            </ControlColumn>

            <VerticalDivider />

            {/* Right: Guide Settings */}
            <ControlColumn>
                <SectionLabel>Guiding</SectionLabel>
                <ParameterRow label="Exposure (s):">
                    <input
                        {...unavailable}
                        id="input-guiding-expose"
                        name="guidingExposure"
                        type="number"
                        step="0.1"
                        min="0.1"
                        className="input input--short"
                        value={guidingExposure}
                        onChange={(e) => !disabled && onGuidingExposureChange?.(parseFloat(e.target.value))}
                        aria-label="Guiding Exposure Seconds"
                    />
                </ParameterRow>
                <ParameterRow label="Gain:">
                    <input
                        {...unavailable}
                        id="input-guiding-gain"
                        name="guidingGain"
                        type="number"
                        step="1"
                        min="0"
                        max="1000"
                        className="input input--short"
                        value={guidingGain}
                        onChange={(e) => !disabled && onGuidingGainChange?.(parseInt(e.target.value, 10))}
                        aria-label="Guiding Gain"
                    />
                </ParameterRow>
            </ControlColumn>
        </ControlContainer>
    );
};
