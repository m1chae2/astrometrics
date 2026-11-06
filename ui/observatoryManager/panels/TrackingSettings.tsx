/**
 * @file TrackingSettings.tsx
 * @description The Alignment and Guiding sections of the Limits tab of the
 * Telescope Control panel: the alignment settings (accuracy, settle time) or
 * the guiding camera settings (exposure, gain). The panel picks which one to
 * show. The values live in the parent, so they survive a tab switch. There is no alignment exposure field,
 * because the alignment start call takes only the target.
 */

import React from 'react';
import { NumberInput } from '../../common/components/NumberInput';
import {
    ControlColumn,
    ParameterRow
} from '../../common/components/ControlLayoutComponents';

/** Which group of settings to show. */
export type TrackingSettingsSection = 'alignment' | 'guiding';

export interface TrackingSettingsProps {
    /** Which settings to show: the alignment ones or the guiding ones. */
    section: TrackingSettingsSection;

    // Alignment settings
    accuracy?: number;
    settleTime?: number;
    onAccuracyChange?: (value: number) => void;
    onSettleTimeChange?: (value: number) => void;

    // Guide settings
    guidingExposure?: number;
    guidingGain?: number;
    onGuidingExposureChange?: (value: number) => void;
    onGuidingGainChange?: (value: number) => void;
    /** Starts guiding with the exposure (seconds) and gain entered in the Guiding tab. */
    onGuideStart?: (exposure: number, gain: number) => void;
    /** Stops guiding. */
    onGuideStop?: () => void;

    /** When true every input is unavailable (the app is not allowed to command the device). */
    disabled?: boolean;
    /** The id of the visible text that says why the inputs are unavailable. */
    disabledReasonId?: string;
}

/**
 * TrackingSettings Component
 *
 * Displays and allows editing of one group of telescope settings: the
 * alignment parameters or the guiding camera settings.
 */
export const TrackingSettings: React.FC<TrackingSettingsProps> = ({
    section,
    accuracy = 30,
    settleTime = 1500,
    onAccuracyChange,
    onSettleTimeChange,
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
        <div className="tracking-settings">
            {section === 'alignment' && (
            <ControlColumn>
                <ParameterRow label="Accuracy:">
                    <NumberInput
                        {...unavailable}
                        id="input-tracking-accuracy"
                        name="accuracy"
                        value={accuracy}
                        onChange={(e) => !disabled && onAccuracyChange?.(parseInt(e.target.value, 10))}
                        width="short"
                        size="small"
                        centered
                        aria-label="Alignment Accuracy"
                    />
                </ParameterRow>
                <ParameterRow label="Settle:">
                    <NumberInput
                        {...unavailable}
                        id="input-tracking-settle"
                        name="settleTime"
                        value={settleTime}
                        onChange={(e) => !disabled && onSettleTimeChange?.(parseInt(e.target.value, 10))}
                        width="short"
                        size="small"
                        centered
                        aria-label="Settle Time"
                    />
                </ParameterRow>
            </ControlColumn>
            )}
            {section === 'guiding' && (
            <ControlColumn>
                <ParameterRow label="Exposure (s):">
                    <NumberInput
                        {...unavailable}
                        id="input-guiding-expose"
                        name="guidingExposure"
                        step="0.1"
                        min="0.1"
                        width="short"
                        size="small"
                        centered
                        value={guidingExposure}
                        onChange={(e) => !disabled && onGuidingExposureChange?.(parseFloat(e.target.value))}
                        aria-label="Guiding Exposure Seconds"
                    />
                </ParameterRow>
                <ParameterRow label="Gain:">
                    <NumberInput
                        {...unavailable}
                        id="input-guiding-gain"
                        name="guidingGain"
                        step="1"
                        min="0"
                        max="1000"
                        width="short"
                        size="small"
                        centered
                        value={guidingGain}
                        onChange={(e) => !disabled && onGuidingGainChange?.(parseInt(e.target.value, 10))}
                        aria-label="Guiding Gain"
                    />
                </ParameterRow>
            </ControlColumn>
            )}
        </div>
    );
};
