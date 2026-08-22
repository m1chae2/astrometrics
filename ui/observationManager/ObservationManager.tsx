/**
 * @file ObservationManager.tsx
 * @description Main container for the Observation Manager display. Orchestrates target selection, planning, and execution.
 * REQ: OBM-1: Session Planning
 */
import React from 'react';
import { TargetListPanel } from './TargetListPanel';
import { TargetPlanner } from './TargetPlanner';
import { ExecutionQueue } from './ExecutionQueue';
import { TargetListStatus } from './TargetListStatus';
import { useExecutionQueue, SequenceObject } from './hooks/useExecutionQueue';
import { useTargetContext } from '../common/context/TargetContext';
import { usePlanningContext } from './context/PlanningContext';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { createTarget } from '../common/services/targetService';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import './observationManager.css';

/**
 * Main container for the Observation Manager display.
 * Manages the layout of the three main columns: Target List, Target Planner, and Image Execution.
 * REQ: OBM-1: Session Planning
 * REQ: OBM-4: Execution Queue Management
 * REQ: SR-4.3: The system SHALL execute planned imaging sequences via a queue manager.
 */
export const ObservationManager: React.FC = () => {
    const {
        queue,
        addToQueue,
        removeFromQueue,
        updateQueueItem,
        reorderQueue,
        error,
        clearError
    } = useExecutionQueue();

    // Lifted state for target selection shared with child components
    const {
        selectedTarget,
        setSelectedTarget,
        pendingTarget,
        setPendingTarget,
        reloadKey,
        forceReload
    } = useTargetContext();

    // Use PlanningContext for sequence state (building out an observation plan)
    const {
        planItems,
        addItem,
        addItems,
        updateItem,
        removeItem,
        clearItems,
        setItems
    } = usePlanningContext();

    /** Flag indicating if the 'Add New Target' modal is visible */
    const [isAddModalOpen, setIsAddModalOpen] = React.useState(false);

    /** ID of the execution queue sequence currently being modified, if any */
    const [editingExecutionId, setEditingExecutionId] = React.useState<string | null>(null);

    /** Persists a newly created target and re-fetches the list */
    const handleAddTargetSubmit = async (name: string, ra: string, dec: string) => {
        await createTarget(name, ra, dec);
        forceReload();
    };

    /** Updates the currently focused target for planning and resets edit states */
    const handleTargetChange = (target: string) => {
        setSelectedTarget(target);
        setEditingExecutionId(null);
        clearItems();
    };

    /** Loads an existing sequence from the execution queue back into the planner for editing */
    const handleEditSequence = (sequence: SequenceObject) => {
        setEditingExecutionId(sequence.id);
        setSelectedTarget(sequence.target_name);
        const newItems = sequence.items.map((item: any, idx: number) => ({
            id: `${Date.now()}-${idx}`,
            uuid: `${Date.now()}-${idx}`,
            targetId: sequence.target_name,
            type: item.type || 'Light',
            count: item.count,
            exposure: item.exposure,
            delay: item.delay || 0,
            filter: item.filter
        }));
        setItems(newItems);
    };

    /** Commits the modified plan items back to the active execution queue item */
    const handleUpdateSequence = (targetName: string, items: any[]) => {
        if (editingExecutionId) {
            updateQueueItem(editingExecutionId, targetName, items);
            setEditingExecutionId(null);
            clearItems();
        }
    };

    const handleCancelEdit = () => {
        setEditingExecutionId(null);
        clearItems();
    };

    const handleAddToSequence = (targetName: string, items: any[]) => {
        addToQueue(targetName, items);
        clearItems();
    };

    const leftPanel = (
        // REQ: OBM-1.1: The display SHALL present a selectable list of targets for planning.
        <TargetListPanel
            selectedTarget={selectedTarget}
            setSelectedTarget={handleTargetChange}
            pendingTarget={pendingTarget}
            setPendingTarget={setPendingTarget}
            reloadKey={reloadKey}
            onAddTarget={() => setIsAddModalOpen(true)}
        />
    );

    const centerPanel = (
        // REQ: OBM-1.2: The display SHALL allow the user to select one target as the active subject for the current plan.
        <TargetPlanner
            selectedTargetId={selectedTarget}
            onAddToSequence={editingExecutionId ? handleUpdateSequence : handleAddToSequence}
            onCancelEdit={handleCancelEdit}
            isEditing={!!editingExecutionId}
            planItems={planItems}
            addItem={addItem}
            addItems={addItems}
            updateItem={updateItem}
            removeItem={removeItem}
        />
    );

    const rightPanel = (
        <div className="observation-manager__right-panel">
            <div className="observation-manager__queue">
                <ExecutionQueue
                    queue={queue}
                    removeFromQueue={removeFromQueue}
                    onEdit={handleEditSequence}
                    reorderQueue={reorderQueue}
                />
            </div>
            <div className="observation-manager__status-wrapper visible-targets-wrapper">
                <TargetListStatus />
            </div>
        </div>
    );

    return (
        <>
            {error && <div className="om-error-toast" onClick={clearError}>{error}</div>}

            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={() => setIsAddModalOpen(false)}
                onAdd={handleAddTargetSubmit}
            />

            <GenericDisplayLayout
                leftPanel={leftPanel}
                centerPanel={centerPanel}
                rightPanel={rightPanel}
            />
        </>
    );
};

export default ObservationManager;
