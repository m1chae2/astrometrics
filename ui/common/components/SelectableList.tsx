import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Button } from './Button';
import { EmptyState } from './EmptyState';
import { Badge, type BadgeTone } from './Badge';
import '../../common/styles/listCommon.css';

/** A small badge drawn after a row's name, such as "NB" for a notebook. */
export interface SelectableItemBadge {
    /** The badge text. Keep it to a word or two letters. */
    label: string;
    /** The badge color. Leave it out for neutral grey. */
    tone?: BadgeTone;
    /** Hover text that says what the badge means, such as "Jupyter notebook". */
    title?: string;
}

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
    /** Optional star magnitude, so a caller that opens a details card shows the same value as the row. */
    magnitude?: number;
    /** The library's verdict that `magnitude` is a real catalog magnitude (not missing, 0 or instrumental). */
    hasCatalogMagnitude?: boolean;
    /** Optional catalog spectral type, such as "K3II+B9.5V". */
    spectralType?: string;
    /** True when the target is under the horizon. The row then shows a small muted "Below" badge. */
    isBelowHorizon?: boolean;
    /** Hover text for the "Below" badge, such as "Below the horizon now". */
    belowHorizonTooltip?: string;
    /** Optional badges drawn after the name, for lists that need their own markers. */
    badges?: SelectableItemBadge[];
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
    /** Whether the last fetch failed. Shown only while `items` is empty, so earlier data stays visible. */
    isError?: boolean;
    /** Shown in place of the list when `isError` is true and `items` is empty. */
    errorMessage?: React.ReactNode;
    /** Called by the "Try again" button shown with the error message. */
    onRetry?: () => void;
    /**
     * Lets a long name wrap onto a second line instead of being cut off with "...". Every row
     * then gets the taller height, so the rows stay evenly spaced. Names longer than two lines
     * are still cut off at the end of the second line.
     */
    wrapLabels?: boolean;
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
// A row whose name may wrap to two lines and also has a subtitle under it.
const THREE_LINE_ROW_HEIGHT_PX = 64;

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
    loadingMessage = 'Loading the list',
    emptyMessage = 'No items to display.',
    isError = false,
    errorMessage = 'Could not load the list.',
    onRetry,
    wrapLabels = false,
}) => {
    // Every list needs ids and a radio group name of its own. Two displays can be mounted
    // at once (a hidden display stays mounted) and use the same className, so a name built from
    // the className alone made their rows share ids; clicking a row then switched on the
    // matching row of the other, hidden list and the visible list never saw the pick.
    const listInstanceId = useId();
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const [scrollTop, setScrollTop] = useState(0);
    const [viewportHeight, setViewportHeight] = useState(0);

    useEffect(() => {
        const node = scrollContainerRef.current;
        if (!node) return;

        const updateViewportHeight = () => setViewportHeight(node.clientHeight);
        updateViewportHeight();

        // Older WebViews may lack ResizeObserver; the height is then read once.
        if (typeof ResizeObserver === 'undefined') return;
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
    const rowHeightPx = wrapLabels
        ? (hasSubtitles ? THREE_LINE_ROW_HEIGHT_PX : TWO_LINE_ROW_HEIGHT_PX)
        : (hasSubtitles ? TWO_LINE_ROW_HEIGHT_PX : ROW_HEIGHT_PX);
    const rowHeightClass = wrapLabels
        ? (hasSubtitles ? ' selectable-list__item--three-line' : ' selectable-list__item--two-line')
        : (hasSubtitles ? ' selectable-list__item--two-line' : '');
    const labelTextClass = `selectable-list__label-text${wrapLabels ? ' selectable-list__label-text--wrap' : ''}`;
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
                isError && !isLoading ? (
                    <EmptyState
                        variant="error"
                        message={
                            <>
                                {errorMessage}
                                {onRetry && (
                                    <Button variant="secondary" size="small" className="manager__retry-btn" onClick={onRetry}>
                                        Try again
                                    </Button>
                                )}
                            </>
                        }
                    />
                ) : (
                    <EmptyState variant={isLoading ? 'loading' : 'empty'} message={isLoading ? loadingMessage : emptyMessage} />
                )
            ) : (
            <div className="selectable-list" style={{ position: 'relative', height: totalHeight, display: 'block' }}>
                {visibleItems.map((item, relativeIndex) => {
                    const idx = startIndex + relativeIndex;
                    const radioId = `${listInstanceId}-radio-${idx}`;
                    const isSelected = item.value === pendingId || item.value === selectedId;
                    const isHighlighted = highlightedIds?.has(item.value);

                    return (
                        <label
                            key={`${item.id}-${idx}`}
                            htmlFor={radioId}
                            className={`selectable-list__item${rowHeightClass}${wrapLabels ? ' selectable-list__item--wrap' : ''}${isSelected ? ' selectable-list__item--selected' : ''}${isHighlighted ? ' selectable-list__item--highlighted' : ''}`}
                            style={{ position: 'absolute', top: idx * rowHeightPx, left: 0, right: 0 }}
                            title={item.tooltip}
                        >
                            <span className="radio">
                                <input
                                    id={radioId}
                                    className="radio__input"
                                    type="radio"
                                    name={`selection-${listInstanceId}`}
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
                                        <span className={labelTextClass}>{item.label}</span>
                                        <span className="selectable-list__subtitle">{item.subtitle ?? ''}</span>
                                    </span>
                                ) : (
                                    <span className={labelTextClass}>{item.label}</span>
                                )}
                                {item.badges?.map((badge) => (
                                    <Badge
                                        key={badge.label}
                                        tone={badge.tone}
                                        title={badge.title}
                                        className="selectable-list__row-badge"
                                    >
                                        {badge.label}
                                    </Badge>
                                ))}
                                {item.hasSpectra && (
                                    <span className="selectable-list__badge selectable-list__badge--spectra" title="Has Spectrum Data">S</span>
                                )}
                                {item.hasPhotometry && (
                                    <span className="selectable-list__badge selectable-list__badge--photometry" title="Has Photometry Data">P</span>
                                )}
                                {item.isBelowHorizon && (
                                    <span
                                        className="selectable-list__badge selectable-list__badge--below-horizon"
                                        title={item.belowHorizonTooltip ?? 'Below the horizon'}
                                    >
                                        Below
                                    </span>
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
