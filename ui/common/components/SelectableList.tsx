import React, { useCallback, useEffect, useRef, useState } from 'react';
import { EmptyState } from './EmptyState';
import '../../common/styles/listCommon.css';

export interface SelectableItem {
    id: string;
    label: string;
    value: string; // The raw value (string or object ID)
    hasSpectra?: boolean;
    hasPhotometry?: boolean;
    isProcessed?: boolean;
    /** Optional second line of smaller text under the label, such as a magnitude. */
    subtitle?: string;
    /** Optional hover text, such as the full name when the label is shortened. */
    tooltip?: string;
    /** Optional sky coordinates, in degrees, for a caller that needs to center a map on this item directly. */
    ra?: number;
    dec?: number;
}

export interface SelectableListProps {
    items: SelectableItem[];
    selectedId?: string;
    pendingId?: string;
    onSelect: (id: string) => void;
    className?: string;
    highlightedIds?: Set<string>;
    /** Whether the list's data is still being fetched, for the empty-state message. */
    isLoading?: boolean;
    /** Shown in place of the list while `isLoading` is true and `items` is empty. */
    loadingMessage?: React.ReactNode;
    /** Shown in place of the list once loading has finished and `items` is empty. */
    emptyMessage?: React.ReactNode;
}

// Match .selectable-list__item's CSS min-height (and its --two-line
// modifier). Rows are absolutely positioned at idx * row height, so a
// row's rendered height must never exceed its height or it will visually
// overlap the next row. .selectable-list__label-text and
// .selectable-list__subtitle enforce single-line truncation (rather than
// wrapping) to guarantee that. A list whose items carry a subtitle uses
// the taller row for every row so the rows stay evenly spaced.
const ROW_HEIGHT_PX = 32;
const TWO_LINE_ROW_HEIGHT_PX = 48;

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
    highlightedIds,
    isLoading = false,
    loadingMessage = 'Loading…',
    emptyMessage = 'No items to display.'
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

    const hasSubtitles = items.some((item) => item.subtitle !== undefined);
    const rowHeightPx = hasSubtitles ? TWO_LINE_ROW_HEIGHT_PX : ROW_HEIGHT_PX;
    const totalHeight = items.length * rowHeightPx;
    const firstVisibleIndex = Math.floor(scrollTop / rowHeightPx);
    const startIndex = Math.max(0, firstVisibleIndex - OVERSCAN_ROWS);
    const rowsInViewport = Math.ceil(viewportHeight / rowHeightPx);
    const endIndex = Math.min(items.length, firstVisibleIndex + rowsInViewport + OVERSCAN_ROWS);
    const visibleItems = items.slice(startIndex, endIndex);

    return (
        <div
            className={`manager__list ${className}`}
            ref={scrollContainerRef}
            onScroll={handleScroll}
        >
            {items.length === 0 ? (
                // Rendered inside the same ref'd container as the populated-list
                // branch below (rather than as a separate early return) so the
                // ResizeObserver effect's ref never goes from attached to
                // detached and back -- that effect only runs once on mount, so
                // a list that starts empty and later gets items would otherwise
                // leave `viewportHeight` stuck at its initial 0 forever, which
                // caps rendering at OVERSCAN_ROWS items no matter how tall the
                // container actually is.
                <EmptyState variant={isLoading ? 'loading' : 'empty'} message={isLoading ? loadingMessage : emptyMessage} />
            ) : (
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
                            className={`selectable-list__item${hasSubtitles ? ' selectable-list__item--two-line' : ''}${isSelected ? ' selectable-list__item--selected' : ''}${isHighlighted ? ' selectable-list__item--highlighted' : ''}`}
                            style={{ position: 'absolute', top: idx * rowHeightPx, left: 0, right: 0 }}
                            title={item.tooltip}
                        >
                            <span className="radio">
                                <input
                                    id={radioId}
                                    className="radio__input"
                                    type="radio"
                                    name={`selection-${className}`}
                                    value={item.value}
                                    checked={isSelected}
                                    // A radio's change event never fires for the radio that is
                                    // already checked, so picking the highlighted row again would do
                                    // nothing. Callers that act on a pick (the Planetarium slews to
                                    // the star) need that second pick, and a list pre-checks its
                                    // first row before anything was really chosen. A click event
                                    // fires either way, and arrow-key moves send one too.
                                    onClick={() => onSelect(item.value)}
                                    onChange={() => {}}
                                />
                                <span className="radio__indicator"></span>
                            </span>
                            <span className="selectable-list__label selectable-list__label-content">
                                {hasSubtitles ? (
                                    <span className="selectable-list__label-stack">
                                        <span className="selectable-list__label-text">{item.label}</span>
                                        <span className="selectable-list__subtitle">{item.subtitle ?? ''}</span>
                                    </span>
                                ) : (
                                    <span className="selectable-list__label-text">{item.label}</span>
                                )}
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
            )}
        </div>
    );
};
