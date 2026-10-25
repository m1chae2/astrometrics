/**
 * @file ObservationManager.tsx
 * @description Main container for the Observation Manager display. The Execution Queue spans the top;
 * under it are the package library (left), the package editor (center) and Visible Targets (right).
 * REQ: OBM-1: Session Planning
 */
import React from 'react';
import { PackageLibraryPanel } from './PackageLibraryPanel';
import { PackageEditor } from './PackageEditor';
import { ExecutionQueue } from './ExecutionQueue';
import { TargetListStatus } from './TargetListStatus';
import { useExecutionQueue, SequenceObject } from './hooks/useExecutionQueue';
import { usePackageLibrary, usePackageActions } from './hooks/usePackageLibrary';
import { usePackageDraft } from './hooks/usePackageDraft';
import { findExistingPackage, packageFromSequence, packageToSequenceItems } from './packageModel';
import { useTargetContext } from '../common/context/TargetContext';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { createTarget } from '../common/services/targetService';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { useExecutionQueueQuery } from '../common/queries/useExecutionQueueQuery';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { ObservationPackage } from '../common/types/backendTypes';
import { callBackend } from '../common/services/backendApi';
import './observationManager.css';
import './observationManagerLayout.css';

/**
 * Main container for the Observation Manager display.
 * REQ: OBM-1: Session Planning
 * REQ: OBM-4: Execution Queue Management
 * REQ: SR-4.3: The system SHALL execute planned imaging sequences via a queue manager.
 */
export const ObservationManager: React.FC = () => {
    const { queue, addToQueue, removeFromQueue, updateQueueItem, reorderQueue } = useExecutionQueue();
    // Same shared query useExecutionQueue() is built on; read only for its isLoading flag.
    const { isLoading: isQueueLoading } = useExecutionQueueQuery();
    useReportModeReady('Observation Manager', !isQueueLoading);

    const { setSelectedTarget, setPendingTarget, forceReload } = useTargetContext();
    const library = usePackageLibrary();
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

    const libraryPackage = [...library.lists.suggested, ...library.lists.planned, ...library.lists.complete]
        .find((candidate) => candidate.id === selectedPackageId) ?? pickedSnapshot;
    const editorSource = queueEditPackage ?? libraryPackage;
    const { draft, addExposure, updateExposure, removeExposure, setDither } = usePackageDraft(
        editorSource,
        queueEditPackage ? undefined : editorActions.editPackage
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
     * Opens the target's planned package, else its suggested one, and creates a package only when it
     * has neither. Uses the loaded lists; while they are not loaded, asks the library per target.
     */
    const handleNewPackage = async (targetId: string) => {
        let existing: ObservationPackage | null;
        if (!library.isLoading && !library.error) {
            existing = findExistingPackage(targetId, library.lists.planned, library.lists.suggested);
        } else {
            try {
                const [planned, suggested] = await Promise.all(
                    (['PLANNED', 'SUGGESTED'] as const).map((status) =>
                        callBackend('plan:list_packages', { status, target: targetId }, { silent: true }))
                );
                existing = findExistingPackage(targetId, planned ?? [], suggested ?? []);
            } catch {
                existing = null;
            }
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
                            onDeletePackage={handleDeletePackage}
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
