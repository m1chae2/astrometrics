/**
 * @fileoverview PopoverSelect: the shared single-choice picker. Its menu is drawn in a portal on
 * `document.body` with fixed position, so no panel can hide or clip it. It is a button that opens a small
 * menu of `{id, label, detail?}` entries and reports the id that was picked. It is our own popover,
 * not a native select, so it follows the app's dark theme. Any list can use it. Give it
 * `emptyLabel` to add a first entry that stands for "nothing picked" (it reports null), such as "Live".
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './popoverSelect.css';

/** The gap in pixels between the button and the menu. */
const MENU_GAP_PX = 8;
/** The space in pixels kept between the menu and the window edge. */
const WINDOW_MARGIN_PX = 8;

/** Where the menu is drawn, in window pixels. */
interface MenuPosition {
    left: number;
    top: number;
    /** The button's width, used as the menu's width when `fill` is set. */
    buttonWidth: number;
}

/** One choice the picker can offer. */
export interface PopoverSelectOption {
    /** The value passed to `onSelect` when this entry is picked. */
    id: string;
    /** The main text of the entry. */
    label: string;
    /** Smaller text after the label. Optional. */
    detail?: string;
    /** A tooltip that explains the choice. Optional. It also becomes the button's tooltip while the choice is picked. */
    title?: string;
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
 * focus back on the button. A press outside closes it too. A picked id that is not in `options` is shown as the empty choice.
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
    const menuRef = useRef<HTMLDivElement>(null);
    const [isOpen, setIsOpen] = useState(false);
    const [position, setPosition] = useState<MenuPosition | null>(null);
    const selectedOption = selectedId === null ? undefined : options.find((option) => option.id === selectedId);
    const selectedText = selectedOption ? selectedOption.label : (emptyLabel ?? placeholder);

    const close = useCallback(() => setIsOpen(false), []);

    /** Places the menu next to the button: on the preferred side, or the other side when it does not fit. */
    const updatePosition = useCallback(() => {
        const trigger = triggerRef.current;
        if (!trigger) return;
        const rect = trigger.getBoundingClientRect();
        const menuHeight = menuRef.current?.offsetHeight ?? 0;
        const menuWidth = menuRef.current?.offsetWidth ?? 0;
        const roomBelow = window.innerHeight - rect.bottom - MENU_GAP_PX - WINDOW_MARGIN_PX;
        const roomAbove = rect.top - MENU_GAP_PX - WINDOW_MARGIN_PX;
        const preferAbove = placement === 'above';
        const fitsPreferred = preferAbove ? roomAbove >= menuHeight : roomBelow >= menuHeight;
        const fitsOther = preferAbove ? roomBelow >= menuHeight : roomAbove >= menuHeight;
        const openAbove = fitsPreferred ? preferAbove : (fitsOther ? !preferAbove : roomAbove > roomBelow);
        const top = openAbove ? Math.max(WINDOW_MARGIN_PX, rect.top - MENU_GAP_PX - menuHeight) : rect.bottom + MENU_GAP_PX;
        const maxLeft = Math.max(WINDOW_MARGIN_PX, window.innerWidth - menuWidth - WINDOW_MARGIN_PX);
        const left = Math.min(Math.max(WINDOW_MARGIN_PX, rect.left), maxLeft);
        setPosition({ left, top, buttonWidth: rect.width });
    }, [placement]);

    // Measure and place the menu before the browser paints it, so it never flashes in the wrong spot.
    useLayoutEffect(() => {
        if (isOpen) updatePosition();
        else setPosition(null);
    }, [isOpen, updatePosition]);

    // While open: follow the button on scroll or resize, and close on a press outside the button and menu.
    useEffect(() => {
        if (!isOpen) return;
        const handlePress = (event: MouseEvent) => {
            const target = event.target as Node;
            if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) close();
        };
        document.addEventListener('mousedown', handlePress);
        window.addEventListener('resize', updatePosition);
        window.addEventListener('scroll', updatePosition, true);
        return () => {
            document.removeEventListener('mousedown', handlePress);
            window.removeEventListener('resize', updatePosition);
            window.removeEventListener('scroll', updatePosition, true);
        };
    }, [isOpen, close, updatePosition]);

    const menu = isOpen && (
        <div
            ref={menuRef}
            className={`popover-select__menu${fill ? ' popover-select__menu--fill' : ''}`}
            role="group"
            aria-label={label}
            style={{
                left: position?.left ?? 0,
                top: position?.top ?? 0,
                width: fill && position ? position.buttonWidth : undefined,
                visibility: position ? 'visible' : 'hidden',
            }}
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
                    title={option.title}
                    onClick={() => { onSelect(option.id); close(); }}
                >
                    <span>{option.label}</span>
                    {option.detail && (
                        <span className="popover-select__detail" title={option.detail}>{option.detail}</span>
                    )}
                </button>
            ))}
        </div>
    );

    return (
        <div className={`popover-select${size === 'compact' ? ' popover-select--compact' : ''}${placement === 'above' ? ' popover-select--above' : ''}${fill ? ' popover-select--fill' : ''} ${className}`.trim()}>
            <div className="popover-select__popover-container">
                <button
                    ref={triggerRef}
                    type="button"
                    onClick={() => setIsOpen((previous) => !previous)}
                    className={`popover-select__button${isOpen ? ' popover-select__button--active' : ''}`}
                    aria-haspopup="true"
                    aria-expanded={isOpen}
                    disabled={disabled}
                    aria-label={`${label}: ${selectedText}`}
                    title={selectedOption?.title ?? label}
                >
                    <span>{selectedText}</span>
                    <span className="popover-select__chevron" aria-hidden="true">{isOpen ? '▲' : '▼'}</span>
                </button>
            </div>
            {menu && createPortal(menu, document.body)}
        </div>
    );
};
