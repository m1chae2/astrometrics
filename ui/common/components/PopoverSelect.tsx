/**
 * @fileoverview PopoverSelect: the shared single-choice picker. It is a button that opens a small
 * menu of `{id, label, detail?}` entries and reports the id that was picked. It is our own popover,
 * not a native select, so it follows the app's dark theme. Any list can use it. Give it
 * `emptyLabel` to add a first entry that stands for "nothing picked" (it reports null), such as "Live".
 */

import React, { useRef } from 'react';
import { Popover } from './Popover';
import './popoverSelect.css';

/** One choice the picker can offer. */
export interface PopoverSelectOption {
    /** The value passed to `onSelect` when this entry is picked. */
    id: string;
    /** The main text of the entry. */
    label: string;
    /** Smaller text after the label. Optional. */
    detail?: string;
}

export interface PopoverSelectProps {
    /** The name of the picker for screen readers and the tooltip, such as "Night" or "Camera". */
    label: string;
    /** The choices to offer, in the order to show them. */
    options: PopoverSelectOption[];
    /** The id of the picked choice, or null when the empty choice is picked. */
    selectedId: string | null;
    /** Called with the picked id, or null when the empty choice is picked. */
    onSelect: (id: string | null) => void;
    /**
     * The text of an extra first choice that means "nothing picked". Leave it out for a list
     * that always has a pick. It is also the button text while `selectedId` is null.
     */
    emptyLabel?: string;
    /** The button text when nothing is picked and there is no empty choice. Defaults to "Choose". */
    placeholder?: string;
    /** Extra class names for the outer element, for layout. */
    className?: string;
    /** True to dim the button and stop it opening the menu. */
    disabled?: boolean;
    /** "regular" (the default) matches `.entry`; "compact" is a small button for toolbars. */
    size?: 'regular' | 'compact';
    /** Where the menu opens: "below" (the default) or "above" the button, for pickers at the bottom of a panel. */
    placement?: 'below' | 'above';
    /** Makes the button and menu fill the width of the container. */
    fill?: boolean;
}

/**
 * Shows the picker: a button with the picked choice and a menu of choices.
 * The button names itself as "<label>: <picked text>". Escape closes the menu and puts
 * focus back on the button. A picked id that is not in `options` is shown as the empty choice.
 *
 * @param props See {@link PopoverSelectProps}.
 * @returns The picker.
 */
export const PopoverSelect: React.FC<PopoverSelectProps> = ({
    label,
    options,
    selectedId,
    onSelect,
    emptyLabel,
    placeholder = 'Choose',
    className = '',
    disabled = false,
    size = 'regular',
    placement = 'below',
    fill = false,
}) => {
    const triggerRef = useRef<HTMLButtonElement>(null);
    const selectedOption = selectedId === null ? undefined : options.find((option) => option.id === selectedId);
    const selectedText = selectedOption ? selectedOption.label : (emptyLabel ?? placeholder);

    return (
        <div className={`popover-select${size === 'compact' ? ' popover-select--compact' : ''}${placement === 'above' ? ' popover-select--above' : ''}${fill ? ' popover-select--fill' : ''} ${className}`.trim()}>
            <Popover
                className="popover-select__popover-container"
                trigger={({ isOpen, toggle }) => (
                    <button
                        ref={triggerRef}
                        type="button"
                        onClick={toggle}
                        className={`popover-select__button${isOpen ? ' popover-select__button--active' : ''}`}
                        aria-haspopup="true"
                        aria-expanded={isOpen}
                        disabled={disabled}
                        aria-label={`${label}: ${selectedText}`}
                        title={label}
                    >
                        <span>{selectedText}</span>
                        <span className="popover-select__chevron" aria-hidden="true">{isOpen ? '▲' : '▼'}</span>
                    </button>
                )}
            >
                {({ close }) => (
                    <div
                        className="popover-select__menu"
                        role="group"
                        aria-label={label}
                        onKeyDown={(event) => {
                            if (event.key === 'Escape') {
                                close();
                                triggerRef.current?.focus();
                            }
                        }}
                    >
                        {emptyLabel !== undefined && (
                            <button
                                type="button"
                                className={`popover-select__option${selectedId === null ? ' popover-select__option--selected' : ''}`}
                                aria-pressed={selectedId === null}
                                onClick={() => { onSelect(null); close(); }}
                            >
                                {emptyLabel}
                            </button>
                        )}
                        {options.map((option) => (
                            <button
                                type="button"
                                key={option.id}
                                className={`popover-select__option${selectedId === option.id ? ' popover-select__option--selected' : ''}`}
                                aria-pressed={selectedId === option.id}
                                onClick={() => { onSelect(option.id); close(); }}
                            >
                                <span>{option.label}</span>
                                {option.detail && (
                                    <span className="popover-select__detail" title={option.detail}>{option.detail}</span>
                                )}
                            </button>
                        ))}
                    </div>
                )}
            </Popover>
        </div>
    );
};
