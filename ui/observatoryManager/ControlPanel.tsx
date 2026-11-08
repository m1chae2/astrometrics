import React from 'react';
import { TelescopeControlPanel, type TelescopeCapabilityGates } from './panels/TelescopeControlPanel';
import { GuidingTrendsPlot } from './panels/GuidingStatusPanel';
import { AlignmentGuidingTabs } from './panels/AlignmentGuidingTabs';
import { GuidingSample, AlignmentAttempt } from '../common/types/backendTypes';
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
    onSetTracking: (enabled: boolean) => Promise<unknown> | void;
    onPark: () => Promise<unknown> | void;
    onUnpark: () => Promise<unknown> | void;
    /** Starts the open-ended capture on the target the telescope points at. */
    onStartCapture?: () => Promise<boolean> | boolean | void;
    /** Stops the capture the app started. */
    onStopCapture?: () => Promise<boolean> | boolean | void;
    /** True while a capture job runs. */
    isCapturing?: boolean;
    /** The target the telescope points at, if any. */
    captureTargetName?: string;
    guidingHistory: GuidingSample[];
    alignmentAttempts?: AlignmentAttempt[];

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

    // Alignment control
    onStartAlignment?: () => Promise<unknown> | void;
    onStopAlignment?: () => Promise<unknown> | void;
    /** Why Start Alignment is greyed while no target is picked. Null or empty when it can run. */
    startAlignmentBlockedReason?: string | null;
    isAligningActive?: boolean;

    /** True when the app may not command the equipment. The command controls are then greyed out. */
    commandsDisabled?: boolean;
    /** Why commands are off; shown when the user hovers a greyed control. */
    commandsDisabledReason?: string;
    /** Per-capability gates for the Telescope Control panel. */
    capabilityGates?: TelescopeCapabilityGates;

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
    onStartCapture,
    onStopCapture,
    isCapturing = false,
    captureTargetName,
    guidingHistory,
    alignmentAttempts = [],
    accuracy,
    settleTime,
    onAccuracyChange,
    onSettleTimeChange,
    guidingExposure,
    guidingGain,
    onGuidingExposureChange,
    onGuidingGainChange,
    onStartAlignment,
    onStopAlignment,
    startAlignmentBlockedReason,
    isAligningActive = false,
    commandsDisabled = false,
    commandsDisabledReason = '',
    capabilityGates,
    cameras,
    camerasLoading,
    camerasError,
    onRetryCameras,
}) => {
    return (
        <div className="panel-group">
            {/* Top block, a grid: Telescope Control (with the manual-slew pad on its Manual tab) over Camera
                Profiles, with the Calibration Library on the right spanning both rows. */}
            <div className="observatory-top-block">
                <TelescopeControlPanel
                    isTracking={isTracking}
                    isParked={isParked}
                    onSetTracking={onSetTracking}
                    onPark={onPark}
                    onUnpark={onUnpark}
                    onStartAlignment={onStartAlignment}
                    onStopAlignment={onStopAlignment}
                    startAlignmentBlockedReason={startAlignmentBlockedReason}
                    isAligningActive={isAligningActive}
                    onStartCapture={onStartCapture}
                    onStopCapture={onStopCapture}
                    isCapturing={isCapturing}
                    captureTargetName={captureTargetName}
                    onStartMove={onStartMove}
                    onStopMove={onStopMove}
                    onSetSlewRate={onSetSlewRate}
                    onStop={onStop}
                    accuracy={accuracy}
                    settleTime={settleTime}
                    onAccuracyChange={onAccuracyChange}
                    onSettleTimeChange={onSettleTimeChange}
                    guidingExposure={guidingExposure}
                    guidingGain={guidingGain}
                    onGuidingExposureChange={onGuidingExposureChange}
                    onGuidingGainChange={onGuidingGainChange}
                    commandsDisabled={commandsDisabled}
                    commandsDisabledReason={commandsDisabledReason}
                    capabilityGates={capabilityGates}
                />

                {/* Camera Profiles sits under Telescope Control. It scrolls inside itself. */}
                <div className="observatory-camera-profiles">
                    <CameraProfilesPanel
                        cameras={cameras}
                        loading={camerasLoading}
                        error={camerasError}
                        onRetry={onRetryCameras}
                    />
                </div>

                {/* Calibration Library is tall: it spans both rows. Its table scrolls inside it. */}
                <CalibrationLibraryPanel />
            </div>

            {/* Guiding Row: Trends | Alignment and Guiding (two tabs) */}
            <div className="panel__row flex-fill observatory-guiding-row">
                <GuidingTrendsPlot history={guidingHistory} />
                <AlignmentGuidingTabs alignmentAttempts={alignmentAttempts} guidingHistory={guidingHistory} />
            </div>
        </div>
    );
};
