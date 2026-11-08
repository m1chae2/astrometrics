import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { RadioListFiltering } from '../common/radioList/RadioListFiltering';
import { SelectableList } from '../common/components/SelectableList';
import { TargetListFilterPanel } from '../common/radioList/TargetListFilterPanel';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { ListActions } from '../common/components/ListActions';
import { ControlPanel } from './ControlPanel';
import type { CapabilityGate, TelescopeCapabilityGates } from './panels/TelescopeControlPanel';
import { ObservatoryCapability } from '../common/types/backendTypes';
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
import { IngestFramesModal } from '../common/components/IngestFramesModal';
import { useIngestionManager } from '../common/hooks/useIngestionManager';
import { OBSERVATORY_INGESTION_CHANNEL } from '../common/context/IngestionJobsContext';
import { SessionPicker } from '../common/components/SessionPicker';
import { callBackend } from '../common/services/backendApi';
import { qualityQueryKeys } from '../common/queries/qualityQueryKeys';
import { Tabs, TabPanel, type TabDefinition } from '../common/components/Tabs';
import { CAMERA_ALL } from '../common/hooks/targetListFiltering';
import { ObservatoryNightContext } from './ObservatoryNightContext';
import { useObservatoryNightPicker } from './hooks/useObservatoryNightPicker';

import './observatoryManager.css';
import { Button } from '../common/components/Button';

/**
 * Makes a target id comparable: no underscores or spaces, lower case.
 *
 * @param targetId A target id or name.
 * @returns The comparable text.
 */
const normalizeTargetId = (targetId: string): string => targetId.replace(/[_\s]/g, '').toLowerCase();

/** Why Slew to Target and Start Alignment are greyed while no target is picked. */
const NO_TARGET_REASON = 'Pick a target in the list first.';

/** The two tabs of the Targets panel. List comes first and is the one shown at the start. */
const TARGET_PANEL_TABS: TabDefinition[] = [
    { id: 'list', label: 'List' },
    { id: 'filter', label: 'Filter' },
];

/** Prefix that keeps the Targets tab element ids unique on the page. */
const TARGET_PANEL_TABS_ID_PREFIX = 'observatory-targets';

/**
 * ObservatoryManager component.
 *
 * The main container for the Observatory/Telescope management interface.
 * Orchestrates target selection, telescope control, and status monitoring.
 * A Session panel at the top of the left column chooses what the whole view shows: "Live" (today's
 * behavior) or one past observing night. For a past night, a banner says so, the controls that
 * command equipment or change the library are greyed with the reason, the panels show that
 * night's data, and the Targets panel shows the targets imaged that night (with a way to see all).
 */
export const ObservatoryManager: React.FC = () => {
    const {
        selectedTarget,
        pendingTarget,
        setPendingTarget,
        reloadKey,
        forceReload,
        setSelectedTarget,
    } = useTargetContext();

    const { remoteTargets } = useRemoteStatusContext();
    const {
        items,
        filterPanel,
        filterText,
        setFilterText,
        isLoading,
    } = useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, setSelectedTarget, remoteTargets);

    // Which Targets tab is showing. The filter choices live in the shared filter state, so they
    // are kept when the user goes back to the List tab.
    const [targetsTab, setTargetsTab] = React.useState('list');
    // A short line that says what the list shows, such as "Messier Catalog · Newest".
    const activeFilterText = [
        filterPanel.selectedCatalog,
        filterPanel.selectedCamera === CAMERA_ALL ? null : filterPanel.selectedCamera,
        filterPanel.sort,
    ].filter(Boolean).join(' · ');

    // The night picker at the top of the view. Panels read the choice from the context below.
    const nightPicker = useObservatoryNightPicker();
    const { isLive, nightId, nightDate, night, liveOnlyReason } = nightPicker.value;

    // The night's review gives each target's frame count and warnings for the list's second line.
    const nightQualityQuery = useQuery({
        queryKey: qualityQueryKeys.nightQuality(nightId ?? ''),
        queryFn: () => callBackend('history:night_quality', { session_id: nightId as string }),
        enabled: !isLive && nightId !== null,
        retry: false,
    });

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
    // A capture job in the shared job list means a capture is running. That list is the app's one
    // poll of job progress, so this adds no polling of its own.
    const { connected, activeJobs } = useAstrometrics();
    const isCapturing = activeJobs.some((job) => job.jobType === 'capture');
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

    /**
     * Selects a target when the user clicks it in the list. Download Frames, Slew to Target and
     * Start Alignment all use the selected target, so both the selected and the pending id change.
     * @param targetId The id of the clicked target.
     */
    const handleTargetSelect = React.useCallback((targetId: string) => {
        setSelectedTarget(targetId);
        setPendingTarget(targetId);
    }, [setSelectedTarget, setPendingTarget]);

    // The "Download Frames" window copies frames from the telescope to the library.
    // It starts on the target the user has picked in the list.
    const ingestion = useIngestionManager(selectedTarget, OBSERVATORY_INGESTION_CHANNEL);
    const { openIngestModal, closeIngestModal, isIngestModalOpen, isActive: isDownloading } = ingestion;

    // The download keeps running and finishes in `IngestionJobsContext` (which
    // is always running), so nothing here has to stay awake while this display
    // is hidden: it only shows the state.

    const downloadFramesTitle = isDownloading
        ? 'Frames are being downloaded from the telescope.'
        : !selectedTarget
            ? 'Pick a target in the list first.'
            : 'Copy frames from the telescope into the library.';

    // Equipment commands also stay off while a past night is shown.
    const commandsDisabled = !availability.canCommand || !isLive;
    const commandsDisabledReason = !isLive ? liveOnlyReason : availability.reason;
    // Each group of controls follows its own capability, so a delegated focuser does not grey the mount.
    const gateFor = (capability: ObservatoryCapability): CapabilityGate => ({
        disabled: !availability.canCommandCapability(capability) || !isLive,
        reason: !isLive ? liveOnlyReason : availability.reasonForCapability(capability),
    });
    const capabilityGates: TelescopeCapabilityGates = {
        mount: gateFor(ObservatoryCapability.MOUNT_CONTROL),
        alignment: gateFor(ObservatoryCapability.PLATE_SOLVE_ALIGNMENT),
        guiding: gateFor(ObservatoryCapability.AUTOGUIDING),
        focuser: gateFor(ObservatoryCapability.AUTOFOCUS),
        capture: gateFor(ObservatoryCapability.CAPTURE_ORCHESTRATION),
    };

    // The targets imaged on the chosen night, and the list the panel shows for it.
    const nightTargetKeys = React.useMemo(
        () => new Set((night?.targetsImaged ?? []).map(normalizeTargetId)),
        [night],
    );
    const nightTargetQuality = React.useMemo(() => {
        const byTarget = new Map<string, { framesTaken: number; warningCount: number }>();
        for (const entry of nightQualityQuery.data?.targets ?? []) {
            byTarget.set(normalizeTargetId(entry.targetId), {
                framesTaken: entry.framesTaken,
                warningCount: entry.needsAttention.warningCount,
            });
        }
        return byTarget;
    }, [nightQualityQuery.data]);
    const nightItems = React.useMemo(() => {
        if (isLive) return items;
        const decoratedItems = items.map((item) => {
            const quality = nightTargetQuality.get(normalizeTargetId(item.id));
            if (!quality || !nightTargetKeys.has(normalizeTargetId(item.id))) return item;
            const frames = `${quality.framesTaken} frame${quality.framesTaken === 1 ? '' : 's'}`;
            const warnings = quality.warningCount > 0
                ? `, ${quality.warningCount} warning${quality.warningCount === 1 ? '' : 's'}`
                : '';
            return { ...item, subtitle: `${frames}${warnings}` };
        });
        return decoratedItems.filter((item) => nightTargetKeys.has(normalizeTargetId(item.id)));
    }, [isLive, items, nightTargetKeys, nightTargetQuality]);

    // The left column: Session, Targets (filters, search and list in one panel) and Controls.
    const leftColumn = (
        <div className="manager panel-group observatory-left-column">
            <SectionPanel title="Session" className="flex-auto observatory-session-panel">
                <div className="observatory-session">
                    <SessionPicker
                        label="Night"
                        options={nightPicker.options}
                        selectedId={nightPicker.selectedId}
                        onSelect={nightPicker.select}
                        fill
                    />
                    {/* A status region that is always present, so a screen reader announces the text when it appears. */}
                    <div role="status" className="observatory-night-banner" data-testid="night-banner">
                        {liveOnlyReason}
                    </div>
                </div>
            </SectionPanel>

            <SectionPanel title="Targets" className="flex-fill flex-col observatory-targets-panel">
                <Tabs
                    tabs={TARGET_PANEL_TABS}
                    selectedId={targetsTab}
                    onChange={setTargetsTab}
                    ariaLabel="Targets views"
                    idPrefix={TARGET_PANEL_TABS_ID_PREFIX}
                    size="compact"
                    className="observatory-targets-tabs"
                />
                <TabPanel idPrefix={TARGET_PANEL_TABS_ID_PREFIX} tabId="list" selected={targetsTab === 'list'}>
                    {isLive && (
                        <div className="observatory-targets-panel__active-filter" data-testid="active-filter">
                            {activeFilterText}
                        </div>
                    )}
                    <RadioListFiltering
                        options={[]}
                        selectedOption=""
                        onOptionChange={() => undefined}
                        filterText={filterText}
                        onFilterTextChange={setFilterText}
                    />
                    <SelectableList
                        className="observatory-targets-panel__list"
                        items={nightItems}
                        selectedId={selectedTarget}
                        pendingId={pendingTarget}
                        onSelect={handleTargetSelect}
                        isLoading={isLoading}
                        loadingMessage="Loading targets"
                        emptyMessage={isLive ? 'No targets in the catalog.' : `No targets were imaged on the night of ${nightDate}.`}
                    />
                </TabPanel>
                <TabPanel idPrefix={TARGET_PANEL_TABS_ID_PREFIX} tabId="filter" selected={targetsTab === 'filter'}>
                    <TargetListFilterPanel {...filterPanel} />
                </TabPanel>
            </SectionPanel>

            <SectionPanel
                title="Controls"
                className="flex-auto manager__actions-panel"
                headerContent={<WhyUnavailableButton disabled={commandsDisabled} reason={commandsDisabledReason} />}
            >
                <div className="manager__actions">
                    <ListActions>
                        <Button
                            disabled={!isLive}
                            title={isLive ? undefined : liveOnlyReason}
                            onClick={handlers.onAddTarget}
                        >
                            Add Target
                        </Button>
                        <Button
                            disabled={!isLive || !selectedTarget || isDownloading}
                            title={isLive ? downloadFramesTitle : liveOnlyReason}
                            onClick={openIngestModal}
                        >
                            {isDownloading ? 'Downloading…' : 'Download Frames'}
                        </Button>
                        <CommandGate disabled={commandsDisabled} reason={commandsDisabledReason}>
                            <Button
                                variant="primary"
                                disabled={commandsDisabled || !selectedTarget}
                                title={selectedTarget ? undefined : NO_TARGET_REASON}
                                onClick={() => { if (selectedTarget) void handlers.onSlewToTarget(selectedTarget); }}
                            >
                                Slew to Target
                            </Button>
                        </CommandGate>
                    </ListActions>
                </div>
            </SectionPanel>
        </div>
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
            onStartCapture={() => handlers.onStartCapture(telemetry.targetName)}
            onStopCapture={handlers.onStopCapture}
            isCapturing={isCapturing}
            captureTargetName={telemetry.targetName}
            guidingHistory={telemetry.guidingHistory || []}
            guidingExposure={guidingExposure}
            guidingGain={guidingGain}
            onGuidingExposureChange={setGuidingExposure}
            onGuidingGainChange={setGuidingGain}
            alignmentAttempts={telemetry.alignmentAttempts || []}
            onStartAlignment={() => selectedTarget ? handlers.onStartAlignment(selectedTarget) : undefined}
            startAlignmentBlockedReason={selectedTarget ? null : NO_TARGET_REASON}
            onStopAlignment={handlers.onStopAlignment}
            isAligningActive={telemetry.alignmentActive}
            commandsDisabled={commandsDisabled}
            commandsDisabledReason={commandsDisabledReason}
            capabilityGates={capabilityGates}
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
        <ObservatoryNightContext.Provider value={nightPicker.value}>
            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={() => setIsAddModalOpen(false)}
                onAdd={handlers.onAddTargetSubmit}
            />
            <IngestFramesModal
                isOpen={isIngestModalOpen}
                onClose={closeIngestModal}
                ingestionState={ingestion}
            />
            <GenericDisplayLayout
                className="observatory-display-container"
                leftPanel={leftColumn}
                centerPanel={controlPanel}
                rightPanel={statusPanel}
            />
        </ObservatoryNightContext.Provider>
    );
};
