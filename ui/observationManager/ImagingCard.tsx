/**
 * @file ImagingCard.tsx
 * @description One package in the Execution Queue: target name (a real button that opens and
 * closes the details, so the keyboard works), total time, progress, Edit, Remove and, for the
 * keyboard, Move earlier and Move later. Dragging also reorders, from the Execution Queue.
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
    isExpanded: boolean;
    /** True when this card is the one picked for editing (the display's single selection). */
    isSelected?: boolean;
    onToggle: () => void;
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
 * Shows one queued package as a card.
 * @param props See {@link Props}.
 * @return The card element.
 */
export const ImagingCard: React.FC<Props> = ({ sequence, isExpanded, isSelected = false, onToggle, onRemove, onEdit, progress = null, onMoveEarlier, onMoveLater, ...divProps }) => {
    const [showConfirm, setShowConfirm] = useState(false);

    const totalExposure = sequence.items.reduce((acc: number, item: any) => acc + (item.count * (item.exposure || 0)), 0);
    const totalExposureMins = (totalExposure / 60).toFixed(1);
    const totalExecMins = (sequence.total_duration / 60).toFixed(1);

    const handleRemoveClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        setShowConfirm(true);
    };

    const confirmRemove = () => {
        onRemove(sequence.id);
        setShowConfirm(false);
    };

    const runState = progress ? runStateDisplay(progress.state) : null;
    const isImagingNow = !!progress && (progress.imagingNow ?? false);

    return (
        <div
            {...divProps}
            aria-current={isImagingNow ? 'step' : undefined}
            className={`imaging-card ${isExpanded ? 'expanded' : 'collapsed'} ${isSelected ? 'imaging-card--selected' : ''} ${divProps.className || ''}`}
            data-selected={isSelected ? 'true' : undefined}
            onClick={onToggle}
        >
            <div className="card-header">
                <button
                    type="button"
                    className="card-title"
                    aria-expanded={isExpanded}
                    aria-label={`${sequence.target_name}, ${isExpanded ? 'hide' : 'show'} details`}
                >
                    {sequence.target_name}
                </button>
                <div className="card-meta">
                    <span className="card-duration">TTL: {totalExposureMins}m / {totalExecMins}m</span>
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
            {isExpanded && (
                <div className="card-body" onClick={(e) => e.stopPropagation()}>
                    <div className="card-timing">
                        {/* Read-only: the sequencer sets timing when an entry is added and has no call to change it. */}
                        <span>Timing point: {sequence.timing?.mode === 'at' ? 'Start at a set time' : 'Soonest'}</span>
                    </div>
                    <div className="card-items">
                        <table className="mini-table">
                            <thead>
                                <tr>
                                    <th>Count</th>
                                    <th>Exposure</th>
                                    <th>Delay</th>
                                    <th>Filter</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sequence.items.map((item: any, idx: number) => (
                                    <tr key={idx}>
                                        <td>{item.count}</td>
                                        <td>{item.exposure}s</td>
                                        <td>{item.delay || 0}s</td>
                                        <td>{item.filter}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
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
