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
}) => {
    return (
        <ControlContainer>
            {/* Left: Alignment Settings */}
            <ControlColumn>
                <SectionLabel>Alignment</SectionLabel>
                <ParameterRow label="Accuracy:">
                    <input
                        id="input-tracking-accuracy"
                        name="accuracy"
                        type="number"
                        value={accuracy}
                        onChange={(e) => onAccuracyChange?.(parseInt(e.target.value, 10))}
                        className="input input--short"
                        aria-label="Alignment Accuracy"
                    />
                </ParameterRow>
                <ParameterRow label="Settle:">
                    <input
                        id="input-tracking-settle"
                        name="settleTime"
                        type="number"
                        value={settleTime}
                        onChange={(e) => onSettleTimeChange?.(parseInt(e.target.value, 10))}
                        className="input input--short"
                        aria-label="Settle Time"
                    />
                </ParameterRow>
                <ParameterRow label="Exposure (s):">
                    <input
                        id="input-tracking-expose"
                        name="alignmentExposure"
                        type="number"
                        value={alignmentExposure}
                        onChange={(e) => onAlignmentExposureChange?.(parseFloat(e.target.value))}
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
                        id="input-guiding-expose"
                        name="guidingExposure"
                        type="number"
                        step="0.1"
                        min="0.1"
                        className="input input--short"
                        value={guidingExposure}
                        onChange={(e) => onGuidingExposureChange?.(parseFloat(e.target.value))}
                        aria-label="Guiding Exposure Seconds"
                    />
                </ParameterRow>
                <ParameterRow label="Gain:">
                    <input
                        id="input-guiding-gain"
                        name="guidingGain"
                        type="number"
                        step="1"
                        min="0"
                        max="1000"
                        className="input input--short"
                        value={guidingGain}
                        onChange={(e) => onGuidingGainChange?.(parseInt(e.target.value, 10))}
                        aria-label="Guiding Gain"
                    />
                </ParameterRow>
            </ControlColumn>
        </ControlContainer>
    );
};
