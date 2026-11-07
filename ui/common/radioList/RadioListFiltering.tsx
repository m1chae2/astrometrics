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
}) => {
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
            <TextInput
                placeholder={placeholder}
                value={filterText}
                onChange={(e) => onFilterTextChange(e.target.value)}
            />
        </div>
    );
};
