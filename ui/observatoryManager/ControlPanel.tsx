import React from 'react';
import { DirectionalControl } from './panels/DirectionalControl';
import { TelescopeControl } from './panels/TelescopeControl';
import { TrackingSettings } from './panels/TrackingSettings';
import { LimitSettings } from './panels/LimitSettings';
import { FocuserControl } from './panels/FocuserControl';
import { FilterWheelControl } from './panels/FilterWheelControl';
import { GuidingTrendsPlot, GuidingTargetPlot } from './panels/GuidingStatusPanel';
import { AlignmentStatus } from './panels/AlignmentStatus';
import { GuidingSample, AlignmentAttempt } from '../common/types/backendTypes';
import { SectionPanel } from '../common/components/SectionPanel';
import '../common/styles/scrollbars.css';
import '../common/styles/panels.css';

export interface ControlPanelProps {
    // Pass-through props
    isTracking: boolean;
    isParked: boolean;
    minimumAltitudeLimit?: number;
    maximumAltitudeLimit?: number;
    maxHourAngle?: number;
    meridianFlipHourAngle?: number;

    onStartMove: (direction: string) => void;
    onStopMove: (direction: string) => void;
    onSetSlewRate: (rate: number) => void;
    onStop: () => void;
    onSetTracking: (enabled: boolean) => void;
    onPark: () => void;
    onUnpark: () => void;
    onApplyLimits: (
        enabled: boolean,
        minimumAltitude: number,
        maximumAltitude: number,
        hourAngleEnabled: boolean,
        maximumHourAngle: number,
        flipHourAngle: number
    ) => void;
    onMoveFocuser: (steps: number) => void;
    focuserPosition: number;
    activeFilter: string;
    onSelectFilter: (filter: string) => void;
    guidingHistory: GuidingSample[];
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
    onGuideStart?: () => void;
    onGuideStop?: () => void;
    onGuideCapture?: () => void;

    // Alignment control
    onStartAlignment?: () => void;
    onStopAlignment?: () => void;
    isAligningActive?: boolean;
}

/**
 * ControlPanel component.
 *
 * Aggregates all telescope and observatory control sub-panels into a single
 * layout component. Handles passing down props and callback functions to
 * individual control widgets.
 */
export const ControlPanel: React.FC<ControlPanelProps> = ({
    isTracking,
    isParked,
    minimumAltitudeLimit,
    maximumAltitudeLimit,
    maxHourAngle,
    meridianFlipHourAngle,
    onStartMove,
    onStopMove,
    onSetSlewRate,
    onStop,
    onSetTracking,
    onPark,
    onUnpark,
    onApplyLimits,
    onMoveFocuser,
    focuserPosition,
    activeFilter,
    onSelectFilter,
    guidingHistory,
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
    onGuideStart,
    onGuideStop,
    onGuideCapture,
    onStartAlignment,
    onStopAlignment,
    isAligningActive = false,
}) => {
    return (
        <div className="panel-group">
            {/* Top Row: Telescope Control | Alignment */}
            <div className="panel__row flex-auto">
                <SectionPanel title="Telescope Control">
                    <TelescopeControl
                        isTracking={isTracking}
                        isParked={isParked}
                        onSetTracking={onSetTracking}
                        onPark={onPark}
                        onUnpark={onUnpark}
                        onStartAlignment={onStartAlignment}
                        onStopAlignment={onStopAlignment}
                        isAligningActive={isAligningActive}
                    />
                </SectionPanel>

                {/* REQ: OBS-4: Tracking & Parking Control */}
                <SectionPanel title="Tracking Settings">
                    {/* REQ: OBS-4.3: The display SHALL allow enabling and disabling of sidereal tracking. */}
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
                    />
                </SectionPanel>
            </div>

            {/* Horizontal Middle Row: Focuser | Manual Guide | Safety Limits | Guide Control */}
            <div className="panel__row flex-auto">
                {/* Focuser Control */}
                {/* REQ: OBS-5: Focuser Control */}
                <SectionPanel title="Focuser">
                    <FocuserControl position={focuserPosition} onMoveFocuser={onMoveFocuser} />
                </SectionPanel>

                {/* Manual Guide */}
                {/* REQ: OBS-2: Manual Telescope Movement */}
                <SectionPanel title="Manual Guide">
                    <DirectionalControl
                        onStartMove={onStartMove}
                        onStopMove={onStopMove}
                        onSetSlewRate={onSetSlewRate}
                        onStop={onStop}
                    />
                </SectionPanel>

                {/* Safety Limits */}
                {/* REQ: OBS-8: Safety Limits */}
                <SectionPanel title="Safety Limits">
                    <LimitSettings
                        minAltitude={minimumAltitudeLimit}
                        maxAltitude={maximumAltitudeLimit}
                        maxHourAngle={maxHourAngle}
                        meridianFlipHourAngle={meridianFlipHourAngle}
                        onApplyLimits={onApplyLimits}
                    />
                </SectionPanel>

                {/* Alignment Status */}
                <SectionPanel title="Alignment Status">
                    <AlignmentStatus alignmentAttempts={alignmentAttempts} />
                </SectionPanel>
            </div>

            {/* Horizontal Guiding Row: Trends | Target */}
            <div className="panel__row flex-fill">
                {/* Guiding Trends Panel */}
                <SectionPanel
                    // REQ: OBS-7: Autoguiding Monitoring & Control
                    title="Guiding Trends"
                    className="panel--guiding-trends"
                    headerContent={
                        <span className="guiding-trends__legend">
                            <span className="guiding-trends__legend-item guiding-trends__legend-item--ra">— dRA</span>
                            <span className="guiding-trends__legend-item guiding-trends__legend-item--dec">— dDEC</span>
                        </span>
                    }
                >
                    {/* REQ: OBS-7.3: The display SHALL visualize guiding errors (dRA, dDEC) over time */}
                    <GuidingTrendsPlot history={guidingHistory} />
                </SectionPanel>

                {/* Guiding Target Panel */}
                <SectionPanel title="Guiding Target" className="panel--guiding-target">
                    <GuidingTargetPlot history={guidingHistory} />
                </SectionPanel>
            </div>

            {/* Filter Wheel */}
            {/* REQ: OBS-6: Filter Wheel Control */}
            <SectionPanel title="Filter Wheel" className="panel--filter-wheel flex-auto">
                <FilterWheelControl activeFilter={activeFilter} onSelectFilter={onSelectFilter} />
            </SectionPanel>
        </div>
    );
};
