import React from 'react';
import '../../common/styles/listCommon.css';

export interface SelectableItem {
    id: string;
    label: string;
    value: string; // The raw value (string or object ID)
    hasSpectra?: boolean;
    hasPhotometry?: boolean;
    isProcessed?: boolean;
}

export interface SelectableListProps {
    items: SelectableItem[];
    selectedId?: string;
    pendingId?: string;
    onSelect: (id: string) => void;
    className?: string;
    highlightedIds?: Set<string>;
}

/**
 * Generic component for displaying a list of selectable items with radio buttons.
 * Replaces the duplicated list rendering logic in TargetList and AstronomyList.
 */
export const SelectableList: React.FC<SelectableListProps> = ({
    items,
    selectedId,
    pendingId,
    onSelect,
    className = '',
    highlightedIds
}) => {
    return (
        <div className={`manager__list ${className}`}>
            <div className="selectable-list">
                {items.map((item, idx) => {
                    // Use a unique ID based on className or a random-ish string to prevent collisions
                    const prefix = className ? `${className.trim().replace(/\s+/g, '-')}-` : 'sel-';
                    const radioId = `${prefix}radio-${idx}`;
                    const isSelected = item.value === pendingId || item.value === selectedId;
                    const isHighlighted = highlightedIds?.has(item.value);

                    return (
                        <label
                            key={`${item.id}-${idx}`}
                            htmlFor={radioId}
                            className="selectable-list__item"
                        >
                            <span className="radio">
                                <input
                                    id={radioId}
                                    className="radio__input"
                                    type="radio"
                                    name={`selection-${className}`}
                                    value={item.value}
                                    checked={isSelected}
                                    onChange={() => onSelect(item.value)}
                                />
                                <span className="radio__indicator"></span>
                            </span>
                            <span className="selectable-list__label selectable-list__label-content">
                                {item.label}
                                {item.hasSpectra && (
                                    <span className="selectable-list__badge selectable-list__badge--spectra" title="Has Spectrum Data">S</span>
                                )}
                                {item.hasPhotometry && (
                                    <span className="selectable-list__badge selectable-list__badge--photometry" title="Has Photometry Data">P</span>
                                )}
                                {item.isProcessed === false && (
                                    <span
                                        className="selectable-list__no-processed-badge"
                                        title="No processed image available for this target"
                                    >
                                        no image
                                    </span>
                                )}
                                {isHighlighted && (
                                    <span
                                        className="selectable-list__availability-dot"
                                        title="Available on Telescope"
                                    />
                                )}
                            </span>
                        </label>
                    );
                })}
            </div>
        </div>
    );
};
