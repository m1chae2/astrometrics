import React from 'react';
import { DirectionalControl } from './panels/DirectionalControl';
import { TelescopeControl } from './panels/TelescopeControl';
import { TrackingSettings } from './panels/TrackingSettings';
import { LimitSettings } from './panels/LimitSettings';
import { GuidingTrendsPlot } from './panels/GuidingStatusPanel';
import { AlignmentGuidingTabs } from './panels/AlignmentGuidingTabs';
import { GuidingSample, AlignmentAttempt } from '../common/types/backendTypes';
import { SectionPanel } from '../common/components/SectionPanel';
import { CommandGate, WhyUnavailableButton } from './CommandGate';
import { CalibrationLibraryPanel } from './calibration/CalibrationLibraryPanel';
import { CameraProfilesPanel } from './equipment/CameraProfilesPanel';
import type { CameraProfile } from '../common/hooks/useEquipmentConfiguration';
import '../common/styles/scrollbars.css';
import '../common/styles/panels.css';

export interface ControlPanelProps {
    // Pass-through props
    isTracking: boolean;
    isParked: boolean;

    onStartMove: (direction: string) => void;
    onStopMove: (direction: string) => void;
    onSetSlewRate: (rate: number) => void;
    onStop: () => void;
    onSetTracking: (enabled: boolean) => void;
    onPark: () => void;
    onUnpark: () => void;
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
    onStartMove,
    onStopMove,
    onSetSlewRate,
    onStop,
    onSetTracking,
    onPark,
    onUnpark,
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
            {/* Top Row: Telescope Control | Tracking Settings | Calibration Library */}
            <div className="panel__row flex-auto observatory-top-row">
                <SectionPanel title="Telescope Control" className="observatory-top-row__fit" headerContent={<WhyUnavailableButton disabled={commandsDisabled} reason={commandsDisabledReason} />}>
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

                <SectionPanel title="Tracking Settings" className="observatory-top-row__fit" headerContent={<WhyUnavailableButton disabled={commandsDisabled} reason={commandsDisabledReason} />}>
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

                {/* Calibration Library takes the rest of the row. The Darks table scrolls inside it. */}
                <CalibrationLibraryPanel />
            </div>

            {/* Middle Row: Camera Profiles | Manual Guide | Safety Limits */}
            <div className="panel__row flex-auto observatory-middle-row">
                {/* Camera Profiles, where Focuser used to be. It scrolls inside itself. */}
                <CameraProfilesPanel
                    cameras={cameras}
                    loading={camerasLoading}
                    error={camerasError}
                    onRetry={onRetryCameras}
                />

                {/* Manual Guide */}
                <SectionPanel title="Manual Guide" headerContent={<WhyUnavailableButton disabled={commandsDisabled} reason={commandsDisabledReason} />}>
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

                {/* Safety Limits: owns its panel, because Apply sits in the panel header. */}
                <LimitSettings disabled={commandsDisabled} reason={commandsDisabledReason} />
            </div>

            {/* Guiding Row: Trends | Alignment and Guiding (two tabs) */}
            <div className="panel__row flex-fill observatory-guiding-row">
                <GuidingTrendsPlot history={guidingHistory} />
                <AlignmentGuidingTabs alignmentAttempts={alignmentAttempts} guidingHistory={guidingHistory} />
            </div>
        </div>
    );
};
