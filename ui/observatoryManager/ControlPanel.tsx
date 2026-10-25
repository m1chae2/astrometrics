import React from 'react';
import { DirectionalControl } from './panels/DirectionalControl';
import { TelescopeControl } from './panels/TelescopeControl';
import { TrackingSettings } from './panels/TrackingSettings';
import { LimitSettings } from './panels/LimitSettings';
import { GuidingTrendsPlot, GuidingTargetPlot } from './panels/GuidingStatusPanel';
import { AlignmentStatus } from './panels/AlignmentStatus';
import { GuidingSample, AlignmentAttempt } from '../common/types/backendTypes';
import { SectionPanel } from '../common/components/SectionPanel';
import { CommandGate } from './CommandGate';
import { CalibrationLibraryPanel } from './calibration/CalibrationLibraryPanel';
import { CameraProfilesPanel } from './equipment/CameraProfilesPanel';
import type { CameraProfile } from '../common/hooks/useEquipmentConfiguration';
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

    /** True when the app may not command the equipment. The command controls are then greyed out. */
    commandsDisabled?: boolean;
    /** Why commands are off; shown when the user hovers a greyed control. */
    commandsDisabledReason?: string;

    /** The camera profiles shown in the Camera Profiles panel. */
    cameras: CameraProfile[];
    /** Whether the camera profiles are still loading. */
    camerasLoading: boolean;
    /** The error text of a failed camera read, or null. */
    camerasError: string | null;
    /** Reads the camera profiles again. */
    onRetryCameras: () => void;
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
    commandsDisabled = false,
    commandsDisabledReason = '',
    cameras,
    camerasLoading,
    camerasError,
    onRetryCameras,
}) => {
    return (
        <div className="panel-group">
            {/* Top Row: Telescope Control | Alignment */}
            <div className="panel__row flex-auto">
                <SectionPanel title="Telescope Control">
                    <CommandGate disabled={commandsDisabled} reason={commandsDisabledReason}>
                        <TelescopeControl
                            disabled={commandsDisabled}
                            isTracking={isTracking}
                            isParked={isParked}
                            onSetTracking={onSetTracking}
                            onPark={onPark}
                            onUnpark={onUnpark}
                            onStartAlignment={onStartAlignment}
                            onStopAlignment={onStopAlignment}
                            isAligningActive={isAligningActive}
                        />
                    </CommandGate>
                </SectionPanel>

                {/* REQ: OBS-4: Tracking & Parking Control */}
                <SectionPanel title="Tracking Settings">
                    {/* REQ: OBS-4.3: The display SHALL allow enabling and disabling of sidereal tracking. */}
                    <CommandGate
                        disabled={commandsDisabled}
                        reason={commandsDisabledReason}
                        renderChildren={(reasonId) => (
                            <TrackingSettings
                                disabled={commandsDisabled}
                                disabledReasonId={reasonId}
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
                        )}
                    />
                </SectionPanel>
            </div>

            {/* Horizontal Middle Row: Camera Profiles | Manual Guide | Safety Limits | Alignment Status */}
            <div className="panel__row flex-auto observatory-middle-row">
                {/* Camera Profiles, where Focuser used to be. It scrolls inside itself. */}
                <CameraProfilesPanel
                    cameras={cameras}
                    loading={camerasLoading}
                    error={camerasError}
                    onRetry={onRetryCameras}
                />

                {/* Manual Guide */}
                {/* REQ: OBS-2: Manual Telescope Movement */}
                <SectionPanel title="Manual Guide">
                    <CommandGate disabled={commandsDisabled} reason={commandsDisabledReason}>
                        <DirectionalControl
                            disabled={commandsDisabled}
                            onStartMove={onStartMove}
                            onStopMove={onStopMove}
                            onSetSlewRate={onSetSlewRate}
                            onStop={onStop}
                        />
                    </CommandGate>
                </SectionPanel>

                {/* Safety Limits */}
                {/* REQ: OBS-8: Safety Limits */}
                <SectionPanel title="Safety Limits">
                    <CommandGate
                        disabled={commandsDisabled}
                        reason={commandsDisabledReason}
                        renderChildren={(reasonId) => (
                            <LimitSettings
                                disabled={commandsDisabled}
                                disabledReasonId={reasonId}
                                minAltitude={minimumAltitudeLimit}
                                maxAltitude={maximumAltitudeLimit}
                                maxHourAngle={maxHourAngle}
                                meridianFlipHourAngle={meridianFlipHourAngle}
                                onApplyLimits={onApplyLimits}
                            />
                        )}
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

            {/* Calibration Library, in the short bottom row Filter Wheel used to have. It scrolls inside itself. */}
            <div className="panel__row flex-auto observatory-library-row">
                <CalibrationLibraryPanel />
            </div>
        </div>
    );
};
