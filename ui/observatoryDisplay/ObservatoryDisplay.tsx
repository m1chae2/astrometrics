import React from 'react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { ListActions } from '../common/components/ListActions';
import { ControlPanel } from './ControlPanel';
import { IndiStatusPanel } from './IndiStatusPanel';
import { useTargetContext } from '../common/context/TargetContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { SectionPanel } from '../common/components/SectionPanel';
import { useObservatoryControl } from './useObservatoryControl';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';

import './observatoryDisplay.css';

/**
 * ObservatoryDisplay component.
 *
 * The main container for the Observatory/Telescope management interface.
 * The main container for the Observatory/Telescope management interface.
 * Orchestrates target selection, telescope control, and status monitoring.
 * REQ: SR-1.1: The system SHALL provide control of telescope pointing and tracking.
 */
export const ObservatoryDisplay: React.FC = () => {
    const {
        selectedTarget,
        pendingTarget,
        setPendingTarget,
        reloadKey,
        forceReload,
        raShared,
        decShared,
    } = useTargetContext();

    const { remoteTargets } = useRemoteStatusContext();
    const {
        items,
        filterOptions,
        selectedFilterOption,
        setFilterOption,
        filterText,
        setFilterText,
    } = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, undefined, remoteTargets);

    // REQ: OBS-1.5: The display SHALL update position values at a minimum rate of 1Hz.
    const { trackingStatus, telemetry } = useTelescopeStatus();

    // Custom hook for control logic
    const {
        limitsConfig,
        isAddModalOpen,
        setIsAddModalOpen,
        guidingExposure,
        setGuidingExposure,
        guidingGain,
        setGuidingGain,
        handlers
    } = useObservatoryControl(forceReload);

    // Construct Panels
    const targetListPanel = (
        <RadioListManager
            title="Target List"
            // REQ: OBS-3.1: The display SHALL present a selectable list of detailed targets.
            items={items}
            selectedId={selectedTarget}
            pendingId={pendingTarget}
            onSelect={setPendingTarget}
            // REQ: OBS-3.2: The display SHALL allow filtering of the target list by name or type.
            filterOptions={filterOptions}
            selectedFilterOption={selectedFilterOption}
            onFilterOptionChange={setFilterOption}
            filterText={filterText}
            onFilterTextChange={setFilterText}
            actions={
                <ListActions>
                    {/* REQ: OBS-3.3: The display SHALL provide an "Add Target" function */}
                    <button className="btn" onClick={handlers.onAddTarget}>Add Target</button>
                    {/* REQ: OBS-3.4: The display SHALL provide a "Slew to Target" command button */}
                    <button className="btn btn--success" onClick={() => handlers.onSlewToTarget(raShared, decShared)}>Slew to Target</button>
                </ListActions>
            }
        />
    );

    const controlPanel = (
        <ControlPanel
            isTracking={trackingStatus === 'Tracking'}
            isParked={trackingStatus === 'Parked'}
            minimumAltitudeLimit={limitsConfig.minimumAltitude}
            maximumAltitudeLimit={limitsConfig.maximumAltitude}
            maxHourAngle={limitsConfig.maximumHourAngle}
            meridianFlipHourAngle={limitsConfig.flipHourAngle}
            onStartMove={handlers.onStartMove}
            onStopMove={handlers.onStopMove}
            onSetSlewRate={handlers.onSetSlewRate}
            onStop={handlers.onStop}
            onSetTracking={handlers.onSetTracking}
            onPark={handlers.onPark}
            onUnpark={handlers.onUnpark}
            onApplyLimits={handlers.onApplyLimits}
            onMoveFocuser={handlers.onMoveFocuser}
            focuserPosition={telemetry.focuserPosition || 0}
            activeFilter={telemetry.filter}
            onSelectFilter={handlers.onSelectFilter}
            guidingHistory={telemetry.guidingHistory || []}
            onGuideStart={handlers.onGuideStart}
            onGuideStop={handlers.onGuideStop}
            onGuideCapture={handlers.onGuideCapture}
            guidingExposure={guidingExposure}
            guidingGain={guidingGain}
            onGuidingExposureChange={setGuidingExposure}
            onGuidingGainChange={setGuidingGain}
            alignmentAttempts={telemetry.alignmentAttempts || []}
            onStartAlignment={() => handlers.onStartAlignment(raShared, decShared)}
            onStopAlignment={handlers.onStopAlignment}
            isAligningActive={telemetry.alignmentActive}
        />
    );

    const statusPanel = (
        <IndiStatusPanel />
    );

    return (
        <>
            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={() => setIsAddModalOpen(false)}
                onAdd={handlers.onAddTargetSubmit}
            />
            <GenericDisplayLayout
                className="observatory-display-container"
                leftPanel={targetListPanel}
                centerPanel={controlPanel}
                rightPanel={statusPanel}
            />
        </>
    );
};
