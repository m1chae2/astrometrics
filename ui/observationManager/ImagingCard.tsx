/**
 * @file ImagingCard.tsx
 * @description One package in the Execution Queue, kept minimal: target name, total time, run
 * state, frame progress, and the Move earlier, Move later, Edit and Remove buttons. The exposure
 * rows are not repeated here; Edit loads them into the editor, where the Image List shows them.
 * Dragging also reorders, from the Execution Queue.
 */
import React, { useState } from 'react';
import { SequenceObject } from './hooks/useExecutionQueue';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { ProgressBar } from '../common/components/ProgressBar';
import { StatusChip } from '../common/components/StatusChip';
import { EntryProgress } from '../common/types/backendTypes';
import { runStateDisplay } from './packageModel';
import './queueProgress.css';

interface Props extends React.HTMLAttributes<HTMLDivElement> {
    sequence: SequenceObject;
    /** True when this card is the one picked for editing (the display's single selection). */
    isSelected?: boolean;
    onRemove: (id: string) => void;
    onEdit: () => void;
    /** What the library counted for this entry tonight, or null when it has no progress yet. */
    progress?: EntryProgress | null;
    /** Moves the card one place earlier in the queue. Omit for the first card. */
    onMoveEarlier?: () => void;
    /** Moves the card one place later in the queue. Omit for the last card. */
    onMoveLater?: () => void;
}

/**
 * Writes a run time in seconds as a short phrase: "3 min", or "1 h 20 min" from one hour on.
 * @param seconds Total run time in seconds.
 * @return The short phrase.
 */
export function formatTotalTime(seconds: number): string {
    const minutes = Math.max(1, Math.round((seconds || 0) / 60));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/**
 * Shows one queued package as a card.
 * @param props See {@link Props}.
 * @return The card element.
 */
export const ImagingCard: React.FC<Props> = ({ sequence, isSelected = false, onRemove, onEdit, progress = null, onMoveEarlier, onMoveLater, ...divProps }) => {
    const [showConfirm, setShowConfirm] = useState(false);

    const totalTimeText = formatTotalTime(sequence.total_duration);

    const handleRemoveClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        setShowConfirm(true);
    };

    const confirmRemove = () => {
        onRemove(sequence.id);
        setShowConfirm(false);
    };

    /** A click on the card loads it in the editor. A click inside the open confirm dialog does not. */
    const handleCardClick = () => {
        if (!showConfirm) onEdit();
    };

    const runState = progress ? runStateDisplay(progress.state) : null;
    const isImagingNow = !!progress && (progress.imagingNow ?? false);

    return (
        <div
            {...divProps}
            aria-current={isImagingNow ? 'step' : undefined}
            className={`imaging-card ${isSelected ? 'imaging-card--selected' : ''} ${divProps.className || ''}`}
            data-selected={isSelected ? 'true' : undefined}
            onClick={handleCardClick}
        >
            <div className="card-header">
                <span className="card-title">{sequence.target_name}</span>
                <div className="card-meta">
                    <span className="card-duration">{totalTimeText}</span>
                    {onMoveEarlier && (
                        <button
                            type="button"
                            className="card-edit-btn"
                            onClick={(e) => { e.stopPropagation(); onMoveEarlier(); }}
                            aria-label={`Move ${sequence.target_name} earlier`}
                            title="Move earlier"
                        >
                            <span aria-hidden="true">←</span>
                        </button>
                    )}
                    {onMoveLater && (
                        <button
                            type="button"
                            className="card-edit-btn"
                            onClick={(e) => { e.stopPropagation(); onMoveLater(); }}
                            aria-label={`Move ${sequence.target_name} later`}
                            title="Move later"
                        >
                            <span aria-hidden="true">→</span>
                        </button>
                    )}
                    <button
                        type="button"
                        className="card-edit-btn"
                        onClick={(e) => { e.stopPropagation(); onEdit(); }}
                        aria-label={`Edit ${sequence.target_name}`}
                        title="Edit Sequence"
                    >
                        <span aria-hidden="true">✎</span>
                    </button>
                    <button
                        type="button"
                        className="card-remove-btn"
                        onClick={handleRemoveClick}
                        aria-label={`Remove ${sequence.target_name}`}
                        title="Remove Sequence"
                    >
                        <span aria-hidden="true">✕</span>
                    </button>
                </div>
            </div>
            {progress && runState && (
                <div className="card-progress">
                    <StatusChip status={runState.status} label={isImagingNow ? 'Imaging now' : runState.word} />
                    <ProgressBar
                        value={progress.captured}
                        max={progress.requested}
                        unit="frames"
                        label={`Frames captured for ${sequence.target_name}`}
                    />
                    {progress.note && <span className="card-progress__note">{progress.note}</span>}
                </div>
            )}
            <ConfirmDialog
                open={showConfirm}
                title="Remove Sequence"
                message={`Are you sure you want to remove the plan for ${sequence.target_name}?`}
                onConfirm={confirmRemove}
                onCancel={() => setShowConfirm(false)}
            />
        </div>
    );
};
