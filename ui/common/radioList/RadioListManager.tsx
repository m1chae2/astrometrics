import React from 'react';
import { SelectableList, SelectableItem } from '../components/SelectableList';
import { RadioListFiltering } from './RadioListFiltering';
import { SectionPanel } from '../components/SectionPanel';
import '../styles/manager.css';
import { Button } from '../components/Button';

export interface RadioListManagerProps {
    // Data props
    items: SelectableItem[];
    selectedId?: string;
    pendingId?: string;
    onSelect: (id: string) => void;

    // Filter props
    filterOptions?: string[];
    selectedFilterOption?: string;
    onFilterOptionChange?: (val: string) => void;
    filterText?: string;
    onFilterTextChange?: (text: string) => void;
    filterPlaceholder?: string;

    // Actions
    actions?: React.ReactNode;

    /** Optional panel shown above the list, such as the target list's catalog/camera filters. */
    topPanel?: React.ReactNode;
    topPanelTitle?: string;

    // Customization
    className?: string;

    // Pagination props
    page?: number;
    onPageChange?: (newPage: number) => void;
    hasMore?: boolean;
    /** Total number of pages, if known, shown as "Page X / Y" instead of just "Page X". */
    totalPages?: number;

    // New
    highlightedIds?: Set<string>;
    /** Optional key shown under the filters, explaining the badges on each row. */
    legend?: React.ReactNode;
    noWrapper?: boolean;
    title?: string;
    actionsTitle?: string;

    /** Whether `items` is still being fetched, for the empty-state message. */
    isLoading?: boolean;
    /** Shown in place of the list while `isLoading` is true and `items` is empty. */
    loadingMessage?: React.ReactNode;
    /** Shown in place of the list once loading has finished and `items` is empty. */
    emptyMessage?: React.ReactNode;
    /** Whether the last fetch failed; the error shows only while `items` is empty. */
    isError?: boolean;
    /** Shown with a "Try again" button when `isError` is true and `items` is empty. */
    errorMessage?: React.ReactNode;
    /** Called by the "Try again" button. */
    onRetry?: () => void;

    /**
     * Whether the whole column is folded to a thin strip at the left edge. Folding is offered only
     * when `onCollapsedChange` is also given.
     */
    collapsed?: boolean;
    /** Called with the new folded state when the user clicks the first panel's title or the strip. */
    onCollapsedChange?: (collapsed: boolean) => void;
    /** Hover text of the first panel's title button, which folds the column. */
    hideLabel?: string;
    /** Hover text of the strip that shows the column again. */
    showLabel?: string;
}

const noopOptionChange = (): void => {};

/**
 * Generic Manager component that orchestrates filtering, list display, and actions.
 * Replaces TargetListManager and AstronomyListManager.
 */
export const RadioListManager: React.FC<RadioListManagerProps> = ({
    items,
    selectedId,
    pendingId,
    onSelect,
    filterOptions = [],
    selectedFilterOption = '',
    onFilterOptionChange, // Renamed from onOptionChange in the instruction to match the interface
    filterText = '',
    onFilterTextChange,
    filterPlaceholder,
    actions,
    topPanel,
    topPanelTitle = 'Filter',
    className = '',
    page = 1,
    onPageChange,
    hasMore = false,
    totalPages,
    highlightedIds,
    legend,
    noWrapper = false,
    title = 'List',
    actionsTitle = 'Controls',
    isLoading,
    loadingMessage,
    emptyMessage,
    isError,
    errorMessage,
    onRetry,
    collapsed = false,
    onCollapsedChange,
    hideLabel = 'Hide list',
    showLabel = 'Show list',
}) => {
    const content = (
        <>
            {/* Filtering Section - Only render if handlers are provided */}
            {onFilterTextChange && (
                <RadioListFiltering
                    options={filterOptions}
                    selectedOption={selectedFilterOption}
                    onOptionChange={onFilterOptionChange ?? noopOptionChange}
                    filterText={filterText}
                    onFilterTextChange={onFilterTextChange}
                    placeholder={filterPlaceholder}
                />
            )}

            {legend && <div className="manager__legend">{legend}</div>}

            {/* List Section */}
            <SelectableList
                className={className}
                items={items}
                selectedId={selectedId}
                pendingId={pendingId}
                onSelect={onSelect}
                highlightedIds={highlightedIds}
                isLoading={isLoading}
                loadingMessage={loadingMessage}
                emptyMessage={emptyMessage}
                isError={isError}
                errorMessage={errorMessage}
                onRetry={onRetry}
            />

            {/* Pagination Controls */}
            {onPageChange && (
                <div className="manager__pagination">
                    <Button
                        variant="secondary"
                        size="small"
                        onClick={() => onPageChange(Math.max(1, page - 1))}
                        disabled={page <= 1}
                        aria-label="Previous Page"
                    >
                        &larr; Prev
                    </Button>
                    <span className="manager__pagination-label">
                        Page {page}{totalPages !== undefined ? ` / ${totalPages}` : ''}
                    </span>
                    <Button
                        variant="secondary"
                        size="small"
                        onClick={() => onPageChange(page + 1)}
                        disabled={totalPages !== undefined ? page >= totalPages : !hasMore}
                        aria-label="Next Page"
                    >
                        Next &rarr;
                    </Button>
                </div>
            )}
        </>
    );

    if (noWrapper) {
        return (
            <div className={className}>
                {content}
                {actions && (
                    <div className="manager__actions">
                        {actions}
                    </div>
                )}
            </div>
        );
    }

    if (onCollapsedChange && collapsed) {
        // A thin strip: clicking it, or tabbing to it and pressing Enter, shows the column again.
        return (
            <div className={`manager manager--collapsed panel-group ${className}`}>
                <SectionPanel
                    title={topPanel ? topPanelTitle : title}
                    collapsed="right"
                    onTitleClick={() => onCollapsedChange(false)}
                    titleButtonLabel={showLabel}
                >
                    {null}
                </SectionPanel>
            </div>
        );
    }

    return (
        <div className={`manager panel-group ${className}`}>
            {topPanel && (
                <SectionPanel
                    title={topPanelTitle}
                    className="flex-auto manager__top-panel"
                    onTitleClick={onCollapsedChange ? () => onCollapsedChange(true) : undefined}
                    titleButtonLabel={hideLabel}
                >
                    {topPanel}
                </SectionPanel>
            )}

            <SectionPanel
                title={title}
                className="flex-fill flex-col manager__list-panel"
                onTitleClick={onCollapsedChange && !topPanel ? () => onCollapsedChange(true) : undefined}
                titleButtonLabel={hideLabel}
            >
                {content}
            </SectionPanel>

            {actions && (
                <SectionPanel title={actionsTitle} className="flex-auto manager__actions-panel">
                    <div className="manager__actions">
                        {actions}
                    </div>
                </SectionPanel>
            )}
        </div>
    );
};
