import React from 'react';
import { SelectableList, SelectableItem } from '../components/SelectableList';
import { RadioListFiltering } from './RadioListFiltering';
import { SectionPanel } from '../components/SectionPanel';
import '../styles/manager.css';

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
}) => {
    // REQ: GEN-1.1 - Consistent container structure

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
            />

            {/* Pagination Controls */}
            {onPageChange && (
                <div className="manager__pagination">
                    <button
                        type="button"
                        className="manager__pagination-btn"
                        onClick={() => onPageChange(Math.max(1, page - 1))}
                        disabled={page <= 1}
                        aria-label="Previous Page"
                    >
                        &larr; Prev
                    </button>
                    <span className="manager__pagination-label">
                        Page {page}{totalPages !== undefined ? ` / ${totalPages}` : ''}
                    </span>
                    <button
                        type="button"
                        className="manager__pagination-btn"
                        onClick={() => onPageChange(page + 1)}
                        disabled={totalPages !== undefined ? page >= totalPages : !hasMore}
                        aria-label="Next Page"
                    >
                        Next &rarr;
                    </button>
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

    return (
        <div className={`manager panel-group ${className}`}>
            {topPanel && (
                <SectionPanel title={topPanelTitle} className="flex-auto manager__top-panel">
                    {topPanel}
                </SectionPanel>
            )}

            <SectionPanel title={title} className="flex-fill flex-col manager__list-panel">
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
