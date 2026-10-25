import React, { useState, useEffect } from 'react';
import { PlanItem } from './context/PlanningContext';
import { ControlColumn, ParameterRow } from '../common/components/ControlLayoutComponents';
import '../common/styles/panels.css';

export interface ImageConfig {
    type: string;
    count: number;
    exposure: number;
    filter: string;
    delay: number;
}

interface ImagePlannerProps {
    onAdd: (type: string, count: number, exposure: number, filter: string, delay: number) => void;
    editingItem?: PlanItem | null;
    onCancel?: () => void;
    onTestImage?: (type: string, count: number, exposure: number, filter: string, delay: number) => void;
    // Controlled props
    values?: ImageConfig;
    onChange?: (values: ImageConfig) => void;
}

// REQ: OBM-2: Imaging Sequence Configuration
export const ImagePlanner: React.FC<ImagePlannerProps> = ({
    // REQ: OBM-2.7: The display SHALL allow adding configured items to a "Plan List" before committing to the queue.
    onAdd,
    editingItem,
    onCancel,
    onTestImage,
    values,
    onChange
}) => {
    // Local state for independent usage or edit mode
    const [localType, setLocalType] = useState('Light');
    const [localCount, setLocalCount] = useState(1);
    const [localExposure, setLocalExposure] = useState(60);
    const [localFilter, setLocalFilter] = useState('L');
    const [localDelay, setLocalDelay] = useState(0);

    const isEditing = !!editingItem;

    // Determine effective values: Prop values (if provided and not editing), else local state
    const type = (!isEditing && values?.type !== undefined) ? values.type : localType;
    const count = (!isEditing && values?.count !== undefined) ? values.count : localCount;
    const exposure = (!isEditing && values?.exposure !== undefined) ? values.exposure : localExposure;
    const filter = (!isEditing && values?.filter !== undefined) ? values.filter : localFilter;
    const delay = (!isEditing && values?.delay !== undefined) ? values.delay : localDelay;

    useEffect(() => {
        if (editingItem) {
            setLocalType(editingItem.type || 'Light');
            setLocalCount(editingItem.count);
            setLocalExposure(editingItem.exposure);
            setLocalDelay(editingItem.delay || 0);
            setLocalFilter(editingItem.filter);
        } else if (values) {
            // Sync local state with props when switching modes or when values prop changes
            setLocalType(values.type);
            setLocalCount(values.count);
            setLocalExposure(values.exposure);
            setLocalFilter(values.filter);
            setLocalDelay(values.delay);
        } else {
            // Reset local state if neither editingItem nor values are provided
            setLocalType('Light');
            setLocalCount(1);
            setLocalExposure(60);
            setLocalDelay(0);
            setLocalFilter('L');
        }
    }, [editingItem, values]); // Careful with dependency loop if values change often? values object identity might change.
    // Parent should prevent unnecessary recreations or we assume stable values.

    const update = (updates: Partial<{ type: string; count: number; exposure: number; filter: string; delay: number }>) => {
        if (isEditing) {
            if (updates.type !== undefined) setLocalType(updates.type);
            if (updates.count !== undefined) setLocalCount(updates.count);
            if (updates.exposure !== undefined) setLocalExposure(updates.exposure);
            if (updates.filter !== undefined) setLocalFilter(updates.filter);
            if (updates.delay !== undefined) setLocalDelay(updates.delay);
        } else {
            // Update parent via onChange
            if (onChange) {
                onChange({
                    type: updates.type ?? type,
                    count: updates.count ?? count,
                    exposure: updates.exposure ?? exposure,
                    filter: updates.filter ?? filter,
                    delay: updates.delay ?? delay
                });
            }
            // Also update local state for immediate feedback if not fully controlled
            if (updates.type !== undefined) setLocalType(updates.type);
            if (updates.count !== undefined) setLocalCount(updates.count);
            if (updates.exposure !== undefined) setLocalExposure(updates.exposure);
            if (updates.filter !== undefined) setLocalFilter(updates.filter);
            if (updates.delay !== undefined) setLocalDelay(updates.delay);
        }
    };

    const handleAdd = () => {
        onAdd(type, count, exposure, filter, delay);
    };

    const handleTest = () => {
        if (onTestImage) {
            onTestImage(type, count, exposure, filter, delay);
        }
    };

    return (
        <div className="image-planner">
            <ControlColumn>
                <ParameterRow label="Type">
                    {/* REQ: OBM-2.1: The display SHALL allow configuration of Frame Type (Light, Dark, Flat, Bias). */}
                    <select
                        className="dropdown"
                        value={type}
                        onChange={(e) => update({ type: e.target.value })}
                    >
                        <option value="Light">Light</option>
                        <option value="Dark">Dark</option>
                        <option value="Flat">Flat</option>
                        <option value="Bias">Bias</option>
                    </select>
                </ParameterRow>

                <div className="image-planner__grid">
                    <ParameterRow label="Count">
                        {/* REQ: OBM-2.3: The display SHALL allow configuration of Frame Count. */}
                        <input
                            className="entry entry--short"
                            type="number"
                            min="1"
                            value={count}
                            onChange={(e) => update({ count: parseInt(e.target.value) || 0 })}
                        />
                    </ParameterRow>

                    <ParameterRow label="Filter">
                        {/* REQ: OBM-2.4: The display SHALL allow configuration of Filter selection. */}
                        <select
                            className="dropdown"
                            value={filter}
                            onChange={(e) => update({ filter: e.target.value })}
                        >
                            <option value="L">L</option>
                            <option value="R">R</option>
                            <option value="G">G</option>
                            <option value="B">B</option>
                            <option value="Ha">Ha</option>
                            <option value="OIII">OIII</option>
                            <option value="SII">SII</option>
                        </select>
                    </ParameterRow>

                    <ParameterRow label="Exposure">
                        {/* REQ: OBM-2.2: The display SHALL allow configuration of Exposure Time in seconds. */}
                        <input
                            className="entry entry--short"
                            type="number"
                            min="0.0"
                            step="0.1"
                            value={exposure}
                            onChange={(e) => update({ exposure: parseFloat(e.target.value) || 0 })}
                        />
                    </ParameterRow>

                    <ParameterRow label="Delay">
                        {/* REQ: OBM-2.5: The display SHALL allow configuration of a Delay between frames. */}
                        <input
                            className="entry entry--short"
                            type="number"
                            min="0"
                            value={delay}
                            onChange={(e) => update({ delay: parseInt(e.target.value) || 0 })}
                        />
                    </ParameterRow>
                </div>
            </ControlColumn>

            <div className="image-planner__actions">
                {isEditing ? (
                    <>
                        <button
                            className="btn btn--flex-1"
                            onClick={handleAdd}
                            type="button"
                        >
                            Update
                        </button>
                        <button
                            className="btn btn--danger btn--flex-1"
                            onClick={onCancel}
                            type="button"
                        >
                            Cancel
                        </button>
                    </>
                ) : (
                    <button
                        className="btn btn--full-width"
                        onClick={handleAdd}
                        type="button"
                    >
                        Add to List
                    </button>
                )}

                {/* REQ: OBM-2.8: The display SHALL provide a "Test Image" function to capture a single frame with current settings immediately. */}
                <button
                    className="btn btn--secondary btn--full-width"
                    onClick={handleTest}
                    type="button"
                >
                    Test Image
                </button>
            </div>
        </div >
    );
};
