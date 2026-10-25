/**
 * @file ObservationManager.tsx
 * @description Main container for the Observation Manager display. The Execution Queue spans the top;
 * under it are the package library (left), the package editor (center) and Visible Targets (right).
 */
import React from 'react';
import { PackageLibraryPanel } from './PackageLibraryPanel';
import { PackageEditor } from './PackageEditor';
import { ExecutionQueue } from './ExecutionQueue';
import { TargetListStatus } from './TargetListStatus';
import { useExecutionQueue, SequenceObject } from './hooks/useExecutionQueue';
import { usePackageLibrary, usePackageActions } from './hooks/usePackageLibrary';
import { useCurrentSession } from './hooks/useCurrentSession';
import { usePackageDraft } from './hooks/usePackageDraft';
import { packageFromSequence, packageToSequenceItems, refreshNoticeText } from './packageModel';
import { useTargetContext } from '../common/context/TargetContext';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { createTarget } from '../common/services/targetService';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { useExecutionQueueQuery } from '../common/queries/useExecutionQueueQuery';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { ObservationPackage, PackageStatus } from '../common/types/backendTypes';
import { useQuery } from '@tanstack/react-query';
import { PACKAGE_REFRESH_NOTICE_QUERY_KEY, PackageRefreshCounts } from '../common/queries/packageLibraryQueryKeys';
import { callBackend } from '../common/services/backendApi';
import './observationManager.css';
import './observationManagerLayout.css';

/**
 * Main container for the Observation Manager display.
 */
export const ObservationManager: React.FC = () => {
    const { queue, addToQueue, removeFromQueue, updateQueueItem, reorderQueue } = useExecutionQueue();
    // Same shared query useExecutionQueue() is built on; read only for its isLoading flag.
    const { isLoading: isQueueLoading } = useExecutionQueueQuery();
    useReportModeReady('Observation Manager', !isQueueLoading);

    const { setSelectedTarget, setPendingTarget, forceReload } = useTargetContext();
    const library = usePackageLibrary();
    const currentSession = useCurrentSession();
    // The newest refresh result, written by App.tsx when `packages:changed` (kind refreshed) arrives.
    const refreshResult = useQuery<(PackageRefreshCounts & { receivedAt: number }) | null>({
        queryKey: PACKAGE_REFRESH_NOTICE_QUERY_KEY,
        queryFn: () => null,
        enabled: false,
        staleTime: Infinity,
    }).data ?? null;
    const refreshNotice = refreshResult ? refreshNoticeText(refreshResult) : null;
    const libraryActions = usePackageActions();
    const editorActions = usePackageActions();

    /** Id of the library package picked on the left, if any. */
    const [selectedPackageId, setSelectedPackageId] = React.useState<string | null>(null);
    /** The picked package as last seen, used until the library list contains it. */
    const [pickedSnapshot, setPickedSnapshot] = React.useState<ObservationPackage | null>(null);
    /** The queued sequence being modified, shown in the editor as a temporary package. */
    const [queueEditId, setQueueEditId] = React.useState<string | null>(null);
    const [queueEditPackage, setQueueEditPackage] = React.useState<ObservationPackage | null>(null);
    const [isAddModalOpen, setIsAddModalOpen] = React.useState(false);

    /** Text after Refresh suggestions started, shown until the result arrives or the next action. */
    const [refreshStartedText, setRefreshStartedText] = React.useState<string | null>(null);
    /** The notice after a dismissal (with Undo): the dismissed package and the revision to undo with. */
    const [undoState, setUndoState] = React.useState<{ package: ObservationPackage } | null>(null);
    /** The last library copy of the open package, kept so the form survives its removal. */
    const [lastSeenPackage, setLastSeenPackage] = React.useState<ObservationPackage | null>(null);

    const foundPackage = [...library.lists.suggested, ...library.lists.planned, ...library.lists.complete]
        .find((candidate) => candidate.id === selectedPackageId) ?? null;
    if (foundPackage && foundPackage !== lastSeenPackage) setLastSeenPackage(foundPackage);
    // A suggestion open in the editor can be deleted by a refresh. The refresh event names the
    // removed ids; when it names the open suggestion, keep the form and say so.
    const isOpenSuggestionRemoved = !!selectedPackageId && !queueEditPackage
        && (refreshResult?.deletedPackageIds ?? []).includes(selectedPackageId)
        && pickedSnapshot?.id === selectedPackageId && pickedSnapshot.status === PackageStatus.SUGGESTED;
    const keptPackage = lastSeenPackage?.id === selectedPackageId ? lastSeenPackage : null;
    const libraryPackage = foundPackage ?? (isOpenSuggestionRemoved ? keptPackage : null) ?? pickedSnapshot;
    const editorSource = queueEditPackage ?? libraryPackage;
    const { draft, addExposure, updateExposure, removeExposure, setDither } = usePackageDraft(
        editorSource,
        queueEditPackage || isOpenSuggestionRemoved ? undefined : editorActions.editPackage
    );

    /** Persists a newly created target and re-fetches the list. */
    const handleAddTargetSubmit = async (name: string, ra: string, dec: string) => {
        await createTarget(name, ra, dec);
        forceReload();
    };

    /** Picks a package from the library for editing. */
    const handleSelectPackage = (observationPackage: ObservationPackage) => {
        setQueueEditId(null);
        setQueueEditPackage(null);
        setSelectedPackageId(observationPackage.id);
        setPickedSnapshot(observationPackage);
        setPendingTarget(observationPackage.target_id);
        setSelectedTarget(observationPackage.target_id);
    };

    /**
     * Opens the target's planned package, else its suggested one (the library finds it, matching
     * names its own way), and creates a package only when it has neither.
     */
    const handleNewPackage = async (targetId: string) => {
        let existing: ObservationPackage | null;
        try {
            existing = await callBackend('plan:package_for_target', { target: targetId }, { silent: true });
        } catch {
            existing = null;
        }
        if (existing) {
            handleSelectPackage(existing);
            return;
        }
        const created = await libraryActions.createPackage(targetId);
        if (created) handleSelectPackage(created);
    };

    /** Deletes the shown library package (after the editor's confirmation) and clears the selection. */
    const handleDeletePackage = async (shown: ObservationPackage) => {
        const deleted = await editorActions.deletePackage(shown.id, shown.revision);
        if (deleted) {
            setSelectedPackageId(null);
            setPickedSnapshot(null);
        }
    };

    /** Saves the form of a removed suggestion as a new planned package and opens it. */
    const handleSaveAsPlanned = async () => {
        if (!draft) return;
        const created = await editorActions.createPackage(draft.target_id, {
            exposure_requests: (draft.exposure_requests ?? []) as Array<Record<string, any>>,
            ...(draft.dither_config ? { dither_config: draft.dither_config as Record<string, any> } : {}),
        });
        if (created) handleSelectPackage(created);
    };

    /** Dismisses a whole suggestion and offers Undo. */
    const handleDismissPackage = async (observationPackage: ObservationPackage) => {
        const dismissed = await libraryActions.dismissPackage(observationPackage);
        if (dismissed) setUndoState({ package: dismissed });
    };

    /** Takes back the last dismissal. */
    const handleUndoDismiss = async () => {
        if (!undoState) return;
        const restored = await libraryActions.undismissPackage(undoState.package);
        if (restored) setUndoState(null);
    };

    /** Asks the library to refresh the suggestions and says it started. */
    const handleRefreshSuggestions = async () => {
        setRefreshStartedText(null);
        if (await libraryActions.refreshSuggestions()) setRefreshStartedText('Refresh started. Changes will appear here.');
    };

    // The started text goes away when the result arrives.
    React.useEffect(() => {
        if (refreshResult) setRefreshStartedText(null);
    }, [refreshResult]);

    /** Loads a queued sequence into the editor for modification. */
    const handleEditSequence = (sequence: SequenceObject) => {
        // One selection for the whole display: choosing a queue card clears the library choice.
        setSelectedPackageId(null);
        setPickedSnapshot(null);
        setQueueEditId(sequence.id);
        setQueueEditPackage(packageFromSequence(sequence.id, sequence.target_name, sequence.items));
    };

    const handleCancelEdit = () => {
        setQueueEditId(null);
        setQueueEditPackage(null);
    };

    /** Removes a queued sequence; if it was the selected one, the selection is cleared too. */
    const handleRemoveFromQueue = (id: string) => {
        if (id === queueEditId) handleCancelEdit();
        removeFromQueue(id);
    };

    /** Adds the package as edited to the sequencer queue, or modifies the queued sequence. */
    const handleAddToSequence = (edited: ObservationPackage) => {
        const items = packageToSequenceItems(edited);
        if (queueEditId) {
            updateQueueItem(queueEditId, edited.target_id, items);
            handleCancelEdit();
        } else {
            addToQueue(edited.target_id, items);
        }
    };

    return (
        <div className="observation-manager">
            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={() => setIsAddModalOpen(false)}
                onAdd={handleAddTargetSubmit}
            />
            <div className="observation-manager__queue">
                <ExecutionQueue
                    queue={queue}
                    removeFromQueue={handleRemoveFromQueue}
                    selectedId={queueEditId}
                    onEdit={handleEditSequence}
                    reorderQueue={reorderQueue}
                    session={currentSession.session}
                    isSessionLoading={currentSession.isLoading}
                    sessionError={currentSession.error}
                    onRetrySession={currentSession.retry}
                    onTickChecklistItem={(itemId) => {
                        if (currentSession.session) void currentSession.tickChecklistItem(currentSession.session, itemId);
                    }}
                    tickError={currentSession.tickError}
                />
            </div>
            <div className="observation-manager__body">
                <GenericDisplayLayout
                    leftPanel={
                        <PackageLibraryPanel
                            lists={library.lists}
                            isLoading={library.isLoading}
                            error={library.error}
                            onRetry={library.retry}
                            selectedPackageId={selectedPackageId}
                            onSelectPackage={handleSelectPackage}
                            onNewPackage={handleNewPackage}
                            onAddTarget={() => setIsAddModalOpen(true)}
                            actionError={libraryActions.actionError}
                            onDismissActionError={libraryActions.dismissActionError}
                            onDismissPackage={handleDismissPackage}
                            onReopenPackage={(observationPackage) => { void libraryActions.reopenPackage(observationPackage); }}
                            onRefreshSuggestions={handleRefreshSuggestions}
                            refreshStartedText={refreshStartedText}
                            refreshNotice={refreshNotice}
                            undoNotice={undoState ? {
                                text: `Dismissed the suggestion for ${undoState.package.target_id}.`,
                                onUndo: handleUndoDismiss,
                                onClose: () => setUndoState(null),
                            } : null}
                        />
                    }
                    centerPanel={
                        <PackageEditor
                            editedPackage={draft}
                            onAddExposure={addExposure}
                            onUpdateExposure={updateExposure}
                            onRemoveExposure={removeExposure}
                            onChangeDither={setDither}
                            onAddToSequence={handleAddToSequence}
                            isEditingQueueEntry={!!queueEditId}
                            onCancelEdit={handleCancelEdit}
                            errorMessage={editorActions.actionError}
                            onDismissError={editorActions.dismissActionError}
                            onDeletePackage={isOpenSuggestionRemoved ? undefined : handleDeletePackage}
                            removedNotice={isOpenSuggestionRemoved ? {
                                text: 'The library removed this suggestion because it no longer asks for these frames. Your form is still open.',
                                onSave: handleSaveAsPlanned,
                            } : null}
                        />
                    }
                    rightPanel={
                        <div className="observation-manager__right-panel">
                            <TargetListStatus onNewPackage={handleNewPackage} />
                        </div>
                    }
                />
            </div>
        </div>
    );
};

export default ObservationManager;
