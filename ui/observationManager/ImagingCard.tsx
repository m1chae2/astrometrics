import React, { useState } from 'react';
import { SequenceObject } from './hooks/useExecutionQueue';
import { ConfirmDialog } from '../common/components/ConfirmDialog';

interface Props extends React.HTMLAttributes<HTMLDivElement> {
    sequence: SequenceObject;
    isExpanded: boolean;
    onToggle: () => void;
    onRemove: (id: string) => void;
    onEdit: () => void;
}

export const ImagingCard: React.FC<Props> = ({ sequence, isExpanded, onToggle, onRemove, onEdit, ...divProps }) => {
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

    return (
        <div
            {...divProps}
            className={`imaging-card ${isExpanded ? 'expanded' : 'collapsed'} ${divProps.className || ''}`}
            onClick={onToggle}
        >
            <div className="card-header">
                <span className="card-title">{sequence.target_name}</span>
                <div className="card-meta">
                    <span className="card-duration">TTL: {totalExposureMins}m / {totalExecMins}m</span>
                    <button
                        className="card-edit-btn"
                        onClick={(e) => { e.stopPropagation(); onEdit(); }}
                        title="Edit Sequence"
                    >
                        ✎
                    </button>
                    <button
                        className="card-remove-btn"
                        onClick={handleRemoveClick}
                        title="Remove Sequence"
                    >
                        ✕
                    </button>
                </div>
            </div>
            {isExpanded && (
                <div className="card-body" onClick={(e) => e.stopPropagation()}>
                    <div className="card-timing">
                        <label>Timing Point:</label>
                        <select className="dropdown" defaultValue={sequence.timing?.mode || 'soonest'}>
                            <option value="soonest">Soonest</option>
                            <option value="at">Start At...</option>
                        </select>
                        {/* If 'at' selected, show time picker (future scope) */}
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
