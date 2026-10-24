/**
 * @file ObservatoryManager.tsx
 * @description The equipment display: equipment settings, camera profiles with
 * measured noise, the calibration library, device properties (INDI), safety
 * limits, alignment status and the tracking and guiding settings. It has no
 * live control panels; those are in Observation Execution. While Ekos is in
 * control, settings that command a device are unavailable and say why.
 */

import React from 'react';
import { ControlPanel } from './ControlPanel';
import { IndiStatusPanel } from './IndiStatusPanel';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { useObservatoryControl } from './hooks/useObservatoryControl';
import { useCommandAvailability } from './hooks/useCommandAvailability';
import { useEquipmentConfiguration } from '../common/hooks/useEquipmentConfiguration';
import { EquipmentSettingsPanel } from './equipment/EquipmentSettingsPanel';
import { CameraProfilesPanel } from './equipment/CameraProfilesPanel';
import { CalibrationLibraryPanel } from './calibration/CalibrationLibraryPanel';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import { useReportModeReady } from '../common/utils/appBootReadiness';

import './observatoryManager.css';

/** The id of the visible text that explains why device settings are unavailable. */
const COMMAND_REASON_ID = 'observatory-command-reason';

/**
 * The Observatory Manager display.
 * @return The display.
 */
export const ObservatoryManager: React.FC = () => {
    const { telemetry } = useTelescopeStatus();
    // `connected` reflects the actual websocket handshake with the backend, so
    // it is a genuine "this view now has a real data source" signal.
    const { connected } = useAstrometrics();
    useReportModeReady('Observatory Manager', connected);

    const { limitsConfig, guidingExposure, setGuidingExposure, guidingGain, setGuidingGain, handlers } = useObservatoryControl();
    const availability = useCommandAvailability();
    const equipment = useEquipmentConfiguration();

    const leftPanel = (
        <>
            <EquipmentSettingsPanel
                configuration={equipment.configuration}
                availableCameras={equipment.availableCameras}
                loading={equipment.loading}
                error={equipment.error}
                onSelectCamera={equipment.setActiveCamera}
                onRetry={equipment.reload}
            />
            <CameraProfilesPanel
                cameras={equipment.availableCameras}
                loading={equipment.loading}
                error={equipment.error}
                onRetry={equipment.reload}
            />
        </>
    );

    const centerPanel = (
        <>
            <div className="control-mode-banner" id={COMMAND_REASON_ID} data-testid="control-mode-banner">
                <div aria-busy={availability.isChecking || undefined}>{availability.reason}</div>
                {availability.delegations.length > 0 && (
                    <ul>
                        {availability.delegations.map((delegation) => (
                            <li key={delegation.capability}>{delegation.capability}: {delegation.state}</li>
                        ))}
                    </ul>
                )}
                <div>To change who is in control, use the mode setting in Settings.</div>
            </div>
            <ControlPanel
                minimumAltitudeLimit={limitsConfig.minimumAltitude}
                maximumAltitudeLimit={limitsConfig.maximumAltitude}
                maxHourAngle={limitsConfig.maximumHourAngle}
                meridianFlipHourAngle={limitsConfig.flipHourAngle}
                onApplyLimits={handlers.onApplyLimits}
                alignmentAttempts={telemetry.alignmentAttempts || []}
                guidingExposure={guidingExposure}
                guidingGain={guidingGain}
                onGuidingExposureChange={setGuidingExposure}
                onGuidingGainChange={setGuidingGain}
                settingsDisabled={!availability.canCommand}
                settingsDisabledReasonId={COMMAND_REASON_ID}
            />
            <CalibrationLibraryPanel />
        </>
    );

    return (
        <GenericDisplayLayout
            className="observatory-display-container"
            leftPanel={leftPanel}
            centerPanel={centerPanel}
            rightPanel={<IndiStatusPanel />}
        />
    );
};
