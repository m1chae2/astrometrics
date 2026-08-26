import React, { useCallback, useEffect, useRef, useState } from 'react';
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

// Matches .selectable-list__item's CSS min-height. A star's id/name can
// occasionally be long enough to wrap to a second line -- when that
// happens the row grows past this estimate and can visually overlap
// its neighbor by a few pixels, which is the deliberate tradeoff here:
// a rare, minor overlap versus mounting one real DOM node per item.
const ROW_HEIGHT_PX = 32;

// Rendered above/below the visible viewport so a fast scroll or key
// repeat doesn't show blank space for a frame before the next batch
// of rows mounts.
const OVERSCAN_ROWS = 8;

/**
 * Generic component for displaying a list of selectable items with radio buttons.
 * Replaces the duplicated list rendering logic in TargetList and AstronomyList.
 *
 * Only mounts DOM nodes for the rows currently scrolled into view
 * (plus a small overscan buffer), not one per item in `items`. The
 * astronomy catalog's "All" filter can carry hundreds of thousands of
 * stars; mounting a real <label>/<input>/badge cluster for every one
 * of them -- as this component used to -- pegged the renderer's main
 * thread at 100%+ CPU for minutes rather than seconds, which is
 * indistinguishable from the application being frozen. Windowing
 * keeps the mounted node count bounded by viewport height, not by how
 * many stars are in the catalog.
 */
export const SelectableList: React.FC<SelectableListProps> = ({
    items,
    selectedId,
    pendingId,
    onSelect,
    className = '',
    highlightedIds
}) => {
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const [scrollTop, setScrollTop] = useState(0);
    const [viewportHeight, setViewportHeight] = useState(0);

    useEffect(() => {
        const node = scrollContainerRef.current;
        if (!node) return;

        const updateViewportHeight = () => setViewportHeight(node.clientHeight);
        updateViewportHeight();

        const resizeObserver = new ResizeObserver(updateViewportHeight);
        resizeObserver.observe(node);
        return () => resizeObserver.disconnect();
    }, []);

    const handleScroll = useCallback(() => {
        if (scrollContainerRef.current) {
            setScrollTop(scrollContainerRef.current.scrollTop);
        }
    }, []);

    const totalHeight = items.length * ROW_HEIGHT_PX;
    const firstVisibleIndex = Math.floor(scrollTop / ROW_HEIGHT_PX);
    const startIndex = Math.max(0, firstVisibleIndex - OVERSCAN_ROWS);
    const rowsInViewport = Math.ceil(viewportHeight / ROW_HEIGHT_PX);
    const endIndex = Math.min(items.length, firstVisibleIndex + rowsInViewport + OVERSCAN_ROWS);
    const visibleItems = items.slice(startIndex, endIndex);

    return (
        <div
            className={`manager__list ${className}`}
            ref={scrollContainerRef}
            onScroll={handleScroll}
        >
            <div className="selectable-list" style={{ position: 'relative', height: totalHeight, display: 'block' }}>
                {visibleItems.map((item, relativeIndex) => {
                    const idx = startIndex + relativeIndex;
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
                            style={{ position: 'absolute', top: idx * ROW_HEIGHT_PX, left: 0, right: 0 }}
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
