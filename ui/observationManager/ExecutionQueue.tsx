import React, { useState } from 'react';
import { useExecutionQueue, SequenceObject } from './hooks/useExecutionQueue';
import { ImagingCard } from './ImagingCard';
import { ChecklistDialog } from './ChecklistDialog';
import { formatAsOfTime } from './packageModel';
import { ObservationSessionWithProgress } from '../common/types/backendTypes';
import { SectionPanel } from '../common/components/SectionPanel';
import { InfoButton } from '../common/components/InfoButton';
import { useCommandAvailability } from '../common/hooks/useCommandAvailability';
import { beginImagingSession } from '../common/services/sequencerService';
import '../common/styles/panels.css';
import './executionQueue.css';
import './observationManagerLayout.css';
import './queueProgress.css';

interface Props {
    queue: SequenceObject[];
    removeFromQueue: (id: string) => void;
    onEdit: (sequence: SequenceObject) => void;
    /** Id of the queued sequence picked for editing, or null. Only this card shows the selected look. */
    selectedId?: string | null;
    reorderQueue: (queue: SequenceObject[]) => void;
    /** Tonight's session with its progress, or null when none exists yet. */
    session?: ObservationSessionWithProgress | null;
    isSessionLoading?: boolean;
    /** Text of a failed session read, or null. */
    sessionError?: string | null;
    onRetrySession?: () => void;
    /** Called with the item id when the user ticks a checklist item. */
    onTickChecklistItem?: (itemId: string) => void;
    /** Text of a refused tick, or null. */
    tickError?: string | null;
}

/**
 * The Execution Queue strip: the session's packages as cards in a row, with Start Imaging Session.
 * Start Imaging Session is a device command, so it is greyed while Ekos controls the equipment.
 */
export const ExecutionQueue: React.FC<Props> = ({
    queue, removeFromQueue, onEdit, reorderQueue, selectedId = null,
    session = null, isSessionLoading = false, sessionError = null, onRetrySession,
    onTickChecklistItem, tickError = null,
}) => {
    const [isChecklistOpen, setIsChecklistOpen] = useState(false);
    /** The Checklist button, so focus can return to it when the dialog closes. */
    const checklistButtonRef = React.useRef<HTMLButtonElement>(null);
    const progressByEntryId = new Map((session?.progress?.entries ?? []).map((entry) => [entry.entryId, entry]));
    const asOfTime = formatAsOfTime(session?.progress?.asOf);
    const checklist = session?.checklist ?? [];

    /** Closes the checklist dialog and puts focus back on the button that opened it. */
    const closeChecklist = React.useCallback(() => {
        setIsChecklistOpen(false);
        window.setTimeout(() => checklistButtonRef.current?.focus(), 0);
    }, []);

    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [draggedIdx, setDraggedIdx] = useState<number | null>(null);
    const [localQueue, setLocalQueue] = useState<SequenceObject[]>(queue);
    const [isStarting, setIsStarting] = useState(false);
    /** Text of a refused start, shown as an alert until dismissed, or null. */
    const [startError, setStartError] = useState<string | null>(null);
    const availability = useCommandAvailability();
    const canStart = availability.canCommand && !isStarting && queue.length > 0;

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

    /**
     * Moves one queued package one place earlier or later and saves the new order.
     * @param index The package's place in the queue now.
     * @param step -1 for earlier, 1 for later.
     */
    const moveEntry = (index: number, step: -1 | 1) => {
        const target = index + step;
        if (target < 0 || target >= localQueue.length) return;
        const next = [...localQueue];
        const [moved] = next.splice(index, 1);
        next.splice(target, 0, moved);
        setLocalQueue(next);
        reorderQueue(next);
    };

    /** Starts the session. Guarded as well as disabled, so a refused policy never sends the command. */
    const handleStartSession = async () => {
        if (!canStart) return;
        setIsStarting(true);
        setStartError(null);
        try {
            await beginImagingSession();
        } catch (error) {
            setStartError(`Could not start the imaging session: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
            setIsStarting(false);
        }
    };

    return (
        <SectionPanel
            title="Execution Queue"
            className="execution-queue-panel execution-queue-strip"
            headerContent={
                <span className="queue-header">
                    {isSessionLoading && <span className="queue-header__status" aria-busy="true">Loading progress…</span>}
                    {!isSessionLoading && sessionError && (
                        <span className="queue-header__error" role="alert">
                            Could not load progress.
                            <button type="button" className="btn btn--tiny" onClick={onRetrySession}>Try again</button>
                        </span>
                    )}
                    {!isSessionLoading && !sessionError && asOfTime && (
                        <span className="queue-header__status">as of {asOfTime}</span>
                    )}
                    <button
                        type="button"
                        className="btn btn--tiny"
                        ref={checklistButtonRef}
                        onClick={() => setIsChecklistOpen(true)}
                    >
                        Checklist ({checklist.length})
                    </button>
                    <button
                        type="button"
                        className="btn btn--tiny btn--primary"
                        onClick={handleStartSession}
                        disabled={!canStart}
                        aria-disabled={!canStart}
                        title={availability.canCommand ? undefined : availability.reason}
                    >
                        {isStarting ? 'Starting...' : 'Start Imaging Session'}
                    </button>
                    {!availability.canCommand && <InfoButton label="Why unavailable?" text={availability.reason} />}
                </span>
            }
        >
            {startError && (
                <div className="queue-header__error" role="alert">
                    <span>{startError}</span>
                    <button type="button" className="btn btn--tiny" onClick={() => setStartError(null)}>Dismiss</button>
                </div>
            )}
            <div className="queue-list">
                {localQueue.length === 0 && (
                    <div className="queue-empty">
                        No packages in this session yet. Pick a package and choose Add to Sequence.
                    </div>
                )}
                {localQueue.map((item, idx) => (
                    <ImagingCard
                        key={item.id}
                        sequence={item}
                        progress={progressByEntryId.get(item.id) ?? null}
                        onRemove={removeFromQueue}
                        onEdit={() => onEdit(item)}
                        onMoveEarlier={idx > 0 ? () => moveEntry(idx, -1) : undefined}
                        onMoveLater={idx < localQueue.length - 1 ? () => moveEntry(idx, 1) : undefined}
                        isSelected={selectedId === item.id}
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
            </div>
            {isChecklistOpen && (
                <ChecklistDialog
                    items={checklist}
                    hasSession={session !== null}
                    onTick={(itemId) => onTickChecklistItem?.(itemId)}
                    errorMessage={tickError}
                    onClose={closeChecklist}
                />
            )}
        </SectionPanel>
    );
};
