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

    // Customization
    className?: string;

    // New
    highlightedIds?: Set<string>;
    noWrapper?: boolean;
    title?: string;
    actionsTitle?: string;
}

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
    className = '',
    highlightedIds,
    noWrapper = false,
    title = 'List',
    actionsTitle = 'Controls',
}) => {
    // REQ: GEN-1.1 - Consistent container structure

    const content = (
        <>
            {/* Filtering Section - Only render if handlers are provided */}
            {onFilterOptionChange && onFilterTextChange && (
                <RadioListFiltering
                    options={filterOptions}
                    selectedOption={selectedFilterOption}
                    onOptionChange={onFilterOptionChange}
                    filterText={filterText}
                    onFilterTextChange={onFilterTextChange}
                    placeholder={filterPlaceholder}
                />
            )}

            {/* List Section */}
            <SelectableList
                className={className}
                items={items}
                selectedId={selectedId}
                pendingId={pendingId}
                onSelect={onSelect}
                highlightedIds={highlightedIds}
            />
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
