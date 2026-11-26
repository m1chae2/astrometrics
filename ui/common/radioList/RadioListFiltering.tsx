import React from 'react';
import { PopoverSelect } from '../components/PopoverSelect';
import { TextInput } from '../components/TextInput';
import '../styles/manager.css';

export interface RadioListFilteringProps {
    options: string[];
    selectedOption: string;
    onOptionChange: (val: string) => void;
    filterText: string;
    onFilterTextChange: (text: string) => void;
    placeholder?: string;
    /** Optional row of small controls shown under the search box, such as filter and sort pickers. */
    filterRow?: React.ReactNode;
}

/**
 * Generic filter component (Dropdown + Search Text) for RadioListManager.
 */
export const RadioListFiltering: React.FC<RadioListFilteringProps> = ({
    options,
    selectedOption,
    onOptionChange,
    filterText,
    onFilterTextChange,
    placeholder = 'Enter name or ID...',
    filterRow,
}) => {
    const searchBox = (
        <TextInput
            placeholder={placeholder}
            value={filterText}
            onChange={(e) => onFilterTextChange(e.target.value)}
        />
    );
    return (
        <div className="manager__filter">
            {options.length > 0 && (
                <PopoverSelect
                    label="Filter"
                    options={options.map((option) => ({ id: option, label: option }))}
                    selectedId={selectedOption}
                    onSelect={(picked) => { if (picked !== null) onOptionChange(picked); }}
                />
            )}
            {searchBox}
            {filterRow && <div className="manager__filter-row">{filterRow}</div>}
        </div>
    );
};
