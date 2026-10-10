import React from 'react';
import { CustomSelect } from '../components/CustomSelect';
import '../styles/entry.css';
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
                <CustomSelect
                    options={options}
                    value={selectedOption}
                    onChange={onOptionChange}
                />
            )}
            <input
                type="text"
                className="entry"
                placeholder={placeholder}
                value={filterText}
                onChange={(e) => onFilterTextChange(e.target.value)}
            />
        </div>
    );
};
