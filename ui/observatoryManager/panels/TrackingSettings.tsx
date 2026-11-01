/**
 * @file TrackingSettings.tsx
 * @description The Tracking Settings panel body: the alignment settings
 * (accuracy, settle time) and the guiding camera settings (exposure, gain)
 * with the Start Guiding and Stop Guiding buttons, on two tabs, "Alignment"
 * and "Guiding". The tabs follow the WAI-ARIA tab pattern. The values live in
 * the parent, so they survive a tab switch. There is no alignment exposure
 * field, because the alignment start call takes only the target.
 */

import React, { useState } from 'react';
import { TabPanel, Tabs, type TabDefinition } from '../../common/components/Tabs';
import {
    ControlColumn,
    ParameterRow
} from '../../common/components/ControlLayoutComponents';

/** The two tabs of the panel. */
const TRACKING_TABS: TabDefinition[] = [
    { id: 'alignment', label: 'Alignment' },
    { id: 'guiding', label: 'Guiding' },
];

/** Prefix that keeps the tab element ids unique on the page. */
const TRACKING_TABS_ID_PREFIX = 'tracking-settings';

export interface TrackingSettingsProps {
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
 * Displays and allows editing of telescope tracking configurations: the
 * alignment parameters on one tab and the guiding camera settings on the other.
 */
export const TrackingSettings: React.FC<TrackingSettingsProps> = ({
    accuracy = 30,
    settleTime = 1500,
    onAccuracyChange,
    onSettleTimeChange,
    guidingExposure = 1.0,
    guidingGain = 100,
    onGuidingExposureChange,
    onGuidingGainChange,
    onGuideStart,
    onGuideStop,
    disabled = false,
    disabledReasonId,
}) => {
    const unavailable = {
        disabled,
        'aria-disabled': disabled || undefined,
        'aria-describedby': disabled ? disabledReasonId : undefined,
    };
    const [selectedTab, setSelectedTab] = useState('alignment');
    // A cleared field reads as NaN. Guiding does not start on a missing or non-positive value.
    const guidingValuesValid = Number.isFinite(guidingExposure) && guidingExposure > 0
        && Number.isFinite(guidingGain) && guidingGain >= 0;
    return (
        <div className="tracking-settings">
            <Tabs
                tabs={TRACKING_TABS}
                selectedId={selectedTab}
                onChange={setSelectedTab}
                ariaLabel="Tracking setting groups"
                idPrefix={TRACKING_TABS_ID_PREFIX}
            />
            <TabPanel idPrefix={TRACKING_TABS_ID_PREFIX} tabId="alignment" selected={selectedTab === 'alignment'}>
            <ControlColumn>
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
            </ControlColumn>
            </TabPanel>
            <TabPanel idPrefix={TRACKING_TABS_ID_PREFIX} tabId="guiding" selected={selectedTab === 'guiding'}>
            <ControlColumn>
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
                <div className="telescope-control__row">
                    <button
                        id="btn-start-guiding"
                        type="button"
                        className="btn btn--control btn--success"
                        onClick={() => !disabled && guidingValuesValid && onGuideStart?.(guidingExposure, guidingGain)}
                        disabled={disabled || !guidingValuesValid}
                        aria-disabled={disabled || !guidingValuesValid}
                    >
                        Start Guiding
                    </button>
                    <button
                        id="btn-stop-guiding"
                        type="button"
                        className="btn btn--control btn--danger"
                        onClick={() => !disabled && onGuideStop?.()}
                        disabled={disabled}
                        aria-disabled={disabled}
                    >
                        Stop Guiding
                    </button>
                </div>
                {!guidingValuesValid && (
                    <p className="tracking-settings__hint">Enter an exposure above 0 and a gain of 0 or more.</p>
                )}
            </ControlColumn>
            </TabPanel>
        </div>
    );
};
