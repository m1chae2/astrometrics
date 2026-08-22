import React, { useState } from 'react';
import { useExecutionQueue, SequenceObject } from './hooks/useExecutionQueue';
import { ImagingCard } from './ImagingCard';
import { SectionPanel } from '../common/components/SectionPanel';
import { beginImagingSession } from '../common/services/sequencerService';
import '../common/styles/panels.css';
import './executionQueue.css';

interface Props {
    queue: SequenceObject[];
    removeFromQueue: (id: string) => void;
    onEdit: (sequence: SequenceObject) => void;
    reorderQueue: (queue: SequenceObject[]) => void;
}

// REQ: OBM-4: Execution Queue Management
export const ExecutionQueue: React.FC<Props> = ({ queue, removeFromQueue, onEdit, reorderQueue }) => {
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [draggedIdx, setDraggedIdx] = useState<number | null>(null);
    const [localQueue, setLocalQueue] = useState<SequenceObject[]>(queue);
    const [isStarting, setIsStarting] = useState(false);

    // Sync local queue when prop changes from outside (e.g. initial load or other users)
    // but only if we are not currently dragging
    React.useEffect(() => {
        if (draggedIdx === null) {
            setLocalQueue(queue);
        }
    }, [queue, draggedIdx]);

    const toggleExpand = (id: string) => {
        setExpandedId(prev => (prev === id ? null : id));
    };

    const handleDragStart = (e: React.DragEvent, index: number) => {
        setDraggedIdx(index);
        e.dataTransfer.effectAllowed = "move";

        // Make the item being dragged look lighter
        const target = e.currentTarget as HTMLElement;
        setTimeout(() => {
            target.classList.add('dragging');
        }, 0);
    };

    const handleDragEnd = (e: React.DragEvent) => {
        const target = e.currentTarget as HTMLElement;
        target.classList.remove('dragging');

        setDraggedIdx(null);
        setDraggedIdx(null);
        // Persist the final order to the server
        // REQ: OBM-4.2: The display SHALL allow reordering of queued sequences via drag-and-drop.
        reorderQueue(localQueue);
    };

    const handleDragEnter = (e: React.DragEvent, targetIdx: number) => {
        if (draggedIdx === null || draggedIdx === targetIdx) return;

        // Perform the swap in local state
        const newQueue = [...localQueue];
        const [movedItem] = newQueue.splice(draggedIdx, 1);
        newQueue.splice(targetIdx, 0, movedItem);

        const updateState = () => {
            setDraggedIdx(targetIdx);
            setLocalQueue(newQueue);
        };

        // Use View Transition API for smooth layout animations if supported
        if ((document as any).startViewTransition) {
            (document as any).startViewTransition(updateState);
        } else {
            updateState();
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
    };
    const handleStartSession = async () => {
        if (queue.length === 0) return;
        setIsStarting(true);
        try {
            await beginImagingSession();
        } catch (error) {
            console.error("Failed to start imaging session:", error);
        } finally {
            setIsStarting(false);
        }
    };

    return (
        <SectionPanel title="Execution Queue" className="execution-queue-panel flex-auto">
            {/* REQ: OBM-4.1: The display SHALL present a queue of all planned sequences. */}
            <div className="queue-list">
                {localQueue.map((item, idx) => (
                    <ImagingCard
                        key={item.id}
                        sequence={item}
                        // REQ: OBM-4.3: The display SHALL allow removal of sequences from the queue.
                        onRemove={removeFromQueue}
                        // REQ: OBM-4.4: The display SHALL allow editing of an existing sequence in the queue.
                        onEdit={() => onEdit(item)}
                        // REQ: OBM-4.5: The display SHALL indicate the execution status of each sequence (Pending, Running, Completed, Failed).
                        // REQ: OBM-4.6: The display SHALL visualize the progress of the active sequence.
                        isExpanded={expandedId === item.id}
                        onToggle={() => toggleExpand(item.id)}
                        draggable
                        onDragStart={(e) => handleDragStart(e, idx)}
                        onDragEnd={handleDragEnd}
                        onDragEnter={(e) => handleDragEnter(e, idx)}
                        onDragOver={handleDragOver}
                        className={`imaging-card ${expandedId === item.id ? 'expanded' : 'collapsed'} ${draggedIdx === idx ? 'dragging' : ''}`}
                        style={{
                            viewTransitionName: item.id.replace(/[^a-zA-Z0-9]/g, '_')
                        } as React.CSSProperties}
                    />
                ))}
                {Array.from({ length: Math.max(0, 6 - localQueue.length) }).map((_, idx) => (
                    <div key={`empty-${idx}`} className="imaging-card placeholder">
                        <div className="card-header card-header--empty">
                            <span className="card-title">Empty Slot</span>
                        </div>
                    </div>
                ))}
            </div>
            <div className="execution-queue-footer">
                <button
                    className="btn btn--success btn--full-width"
                    onClick={handleStartSession}
                    disabled={isStarting || queue.length === 0}
                >
                    {isStarting ? 'Starting...' : 'Start Imaging Session'}
                </button>
            </div>
        </SectionPanel>
    );
};
