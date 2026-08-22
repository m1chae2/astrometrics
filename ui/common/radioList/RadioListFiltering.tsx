import React from 'react';
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
                <select
                    className="dropdown"
                    value={selectedOption}
                    onChange={(e) => onOptionChange(e.target.value)}
                >
                    {options.map((opt) => (
                        <option key={opt} value={opt}>
                            {opt}
                        </option>
                    ))}
                </select>
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
