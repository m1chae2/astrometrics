/**
 * @file ControlPanel.tsx
 * @description The settings half of the Observatory Manager: tracking and
 * guiding settings, safety limits and alignment status. The live control
 * panels (telescope, directional, focuser, filter wheel, guiding plots) now
 * live in Observation Execution. Tracking and guiding settings are device
 * settings, so they are unavailable while Ekos is in control.
 */

import React from 'react';
import { TrackingSettings } from './panels/TrackingSettings';
import { LimitSettings } from './panels/LimitSettings';
import { AlignmentStatus } from './panels/AlignmentStatus';
import { AlignmentAttempt } from '../common/types/backendTypes';
import { SectionPanel } from '../common/components/SectionPanel';
import '../common/styles/scrollbars.css';
import '../common/styles/panels.css';

/** Properties of {@link ControlPanel}. */
export interface ControlPanelProps {
    minimumAltitudeLimit?: number;
    maximumAltitudeLimit?: number;
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
    alignmentAttempts?: AlignmentAttempt[];

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

    /** True when the tracking and guiding settings cannot be changed now. */
    settingsDisabled?: boolean;
    /** The id of the visible text that says why they cannot be changed. */
    settingsDisabledReasonId?: string;
}

/**
 * Lays out the settings panels of the Observatory Manager.
 * @param props See {@link ControlPanelProps}.
 * @return The panel group.
 */
export const ControlPanel: React.FC<ControlPanelProps> = ({
    minimumAltitudeLimit,
    maximumAltitudeLimit,
    maxHourAngle,
    meridianFlipHourAngle,
    onApplyLimits,
    alignmentAttempts = [],
    accuracy,
    settleTime,
    alignmentExposure,
    onAccuracyChange,
    onSettleTimeChange,
    onAlignmentExposureChange,
    guidingExposure,
    guidingGain,
    onGuidingExposureChange,
    onGuidingGainChange,
    settingsDisabled = false,
    settingsDisabledReasonId,
}) => {
    return (
        <div className="panel-group">
            <div className="panel__row flex-auto">
                <SectionPanel title="Tracking Settings">
                    <TrackingSettings
                        accuracy={accuracy}
                        settleTime={settleTime}
                        alignmentExposure={alignmentExposure}
                        onAccuracyChange={onAccuracyChange}
                        onSettleTimeChange={onSettleTimeChange}
                        onAlignmentExposureChange={onAlignmentExposureChange}
                        guidingExposure={guidingExposure}
                        guidingGain={guidingGain}
                        onGuidingExposureChange={onGuidingExposureChange}
                        onGuidingGainChange={onGuidingGainChange}
                        disabled={settingsDisabled}
                        disabledReasonId={settingsDisabledReasonId}
                    />
                </SectionPanel>
            </div>

            <div className="panel__row flex-auto">
                <SectionPanel title="Safety Limits">
                    <LimitSettings
                        minAltitude={minimumAltitudeLimit}
                        maxAltitude={maximumAltitudeLimit}
                        maxHourAngle={maxHourAngle}
                        meridianFlipHourAngle={meridianFlipHourAngle}
                        onApplyLimits={onApplyLimits}
                    />
                </SectionPanel>

                <SectionPanel title="Alignment Status">
                    <AlignmentStatus alignmentAttempts={alignmentAttempts} />
                </SectionPanel>
            </div>
        </div>
    );
};
