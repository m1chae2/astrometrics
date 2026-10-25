import React from 'react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { ListActions } from '../common/components/ListActions';
import { ControlPanel } from './ControlPanel';
import { IndiStatusPanel } from './IndiStatusPanel';
import { useTargetContext } from '../common/context/TargetContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { SectionPanel } from '../common/components/SectionPanel';
import { useObservatoryControl } from './hooks/useObservatoryControl';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { useCommandAvailability } from '../common/hooks/useCommandAvailability';
import { useEquipmentConfiguration } from '../common/hooks/useEquipmentConfiguration';
import { CommandGate, WhyUnavailableButton } from './CommandGate';

import './observatoryManager.css';

/**
 * ObservatoryManager component.
 *
 * The main container for the Observatory/Telescope management interface.
 * Orchestrates target selection, telescope control, and status monitoring.
 */
export const ObservatoryManager: React.FC = () => {
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
        filterPanel,
        filterText,
        setFilterText,
        isLoading,
    } = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, undefined, remoteTargets);

    const { trackingStatus, telemetry } = useTelescopeStatus();
    // Equipment commands stay off until the library says this app may send them.
    const availability = useCommandAvailability();
    const equipment = useEquipmentConfiguration();
    // No fetch-once "loading" concept here (this view is live telemetry, not a
    // one-shot data load, and AstrometricsContext seeds `telescope` with a
    // placeholder object rather than null/undefined, so there's no clean
    // "first real telemetry" signal to key off). `connected` reflects the
    // actual websocket handshake with the backend, so it's a genuine
    // "this view now has a real data source" signal rather than firing the
    // instant the component mounts.
    const { connected } = useAstrometrics();
    useReportModeReady('Observatory Manager', connected);

    // Custom hook for control logic
    const {
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
            items={items}
            selectedId={selectedTarget}
            pendingId={pendingTarget}
            onSelect={setPendingTarget}
            topPanel={<TargetListFilterPanel {...filterPanel} />}
            topPanelTitle="Filter Targets"
            filterText={filterText}
            onFilterTextChange={setFilterText}
            isLoading={isLoading}
            loadingMessage="Loading targets…"
            emptyMessage="No targets in the catalog."
            actions={
                <ListActions>
                    <button className="btn" onClick={handlers.onAddTarget}>Add Target</button>
                    <CommandGate disabled={!availability.canCommand} reason={availability.reason}>
                        <button
                            className="btn btn--primary"
                            disabled={!availability.canCommand}
                            aria-disabled={!availability.canCommand}
                            onClick={() => {
                                if (!availability.canCommand) return;
                                void handlers.onSlewToTarget(raShared, decShared);
                            }}
                        >
                            Slew to Target
                        </button>
                    </CommandGate>
                    <WhyUnavailableButton disabled={!availability.canCommand} reason={availability.reason} />
                </ListActions>
            }
        />
    );

    const controlPanel = (
        <ControlPanel
            isTracking={trackingStatus === 'Tracking'}
            isParked={trackingStatus === 'Parked'}
            onStartMove={handlers.onStartMove}
            onStopMove={handlers.onStopMove}
            onSetSlewRate={handlers.onSetSlewRate}
            onStop={handlers.onStop}
            onSetTracking={handlers.onSetTracking}
            onPark={handlers.onPark}
            onUnpark={handlers.onUnpark}
            guidingHistory={telemetry.guidingHistory || []}
            guidingExposure={guidingExposure}
            guidingGain={guidingGain}
            onGuidingExposureChange={setGuidingExposure}
            onGuidingGainChange={setGuidingGain}
            alignmentAttempts={telemetry.alignmentAttempts || []}
            onStartAlignment={() => handlers.onStartAlignment(raShared, decShared)}
            onStopAlignment={handlers.onStopAlignment}
            isAligningActive={telemetry.alignmentActive}
            commandsDisabled={!availability.canCommand}
            commandsDisabledReason={availability.reason}
            cameras={equipment.availableCameras}
            camerasLoading={equipment.loading}
            camerasError={equipment.error}
            onRetryCameras={equipment.reload}
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
