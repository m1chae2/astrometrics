/**
 * @fileoverview SplitButton: one fused control with two parts. The left part is a normal `Button`
 * that does the main action. The narrow right part is an arrow that opens a menu of other actions.
 * The menu is drawn in a portal on `document.body` (so no scrolling panel clips it) and is placed by
 * `useAnchoredMenu`, the same code `PopoverSelect` uses. This file adds the open state, the menu look,
 * the arrow-key handling and the screen-reader roles.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button, type ButtonSize, type ButtonVariant } from './Button';
import { useAnchoredMenu } from './useAnchoredMenu';
import './splitButton.css';

/** One action offered in the menu of a split button. */
export interface SplitButtonOption {
    /** The value passed to `onSelect` when this entry is picked. */
    id: string;
    /** The main text of the entry. */
    label: string;
    /** Smaller text under the label that explains the entry. Optional. */
    detail?: string;
    /** A tooltip for the entry. For a disabled entry, it should say why the entry is off. */
    title?: string;
    /** True to show the entry dimmed and ignore picks. It stays in the menu so its reason can be read. */
    disabled?: boolean;
}

export interface SplitButtonProps {
    /** The text of the main part. */
    label: string;
    /** Called when the main part is clicked. */
    onClick: () => void;
    /** The entries of the menu, in the order to show them. */
    options: SplitButtonOption[];
    /** Called with the id of the entry that was picked. */
    onSelect: (id: string) => void;
    /** The fill, the same as `Button`. Leave it out for the plain blue button. */
    variant?: ButtonVariant;
    /** The size, the same as `Button`. Leave it out for the regular size. */
    size?: ButtonSize;
    /** True to dim both parts and stop them working. */
    disabled?: boolean;
    /** True while the action runs: dims both parts and shows the same busy look as `Button`'s `pending`. */
    busy?: boolean;
    /** The name of the arrow for screen readers, such as "More analysis options". */
    menuLabel: string;
    /** A tooltip for the main part. Optional. */
    title?: string;
    /** Makes the whole control fill the width of its container. The main part grows; the arrow keeps its width. */
    fill?: boolean;
    /** Extra class names for the outer element, for layout. */
    className?: string;
}

/** The props of the menu body, which is only drawn while the menu is open. */
interface SplitButtonMenuProps {
    /** The entries to show. */
    options: SplitButtonOption[];
    /** The name of the menu for screen readers. */
    menuLabel: string;
    /** Called with the id of an enabled entry that was picked. */
    onPick: (id: string) => void;
    /** Called when Escape is pressed. */
    onEscape: () => void;
    /** True once the menu has its place on screen. Focus moves in only then, because a hidden element cannot take focus. */
    ready: boolean;
    /** The left, top and minimum width of the menu in window pixels, or null before it is measured. */
    placement: { left: number; top: number; minWidth: number } | null;
    /** The element the menu is attached to, so the shared placement code can measure the menu. */
    menuRef: React.RefObject<HTMLDivElement | null>;
}

/**
 * Draws the menu. It puts focus on the first enabled entry when it appears, moves focus with the
 * Up and Down keys (skipping disabled entries) and reports Escape. Enter and Space click the focused
 * entry, because each entry is a native button.
 *
 * @param props See {@link SplitButtonMenuProps}.
 * @returns The menu element.
 */
const SplitButtonMenu: React.FC<SplitButtonMenuProps> = ({
    options, menuLabel, onPick, onEscape, ready, placement, menuRef,
}) => {

    /** Lists the entries a keyboard user can reach, which are the ones that are not disabled. */
    const enabledItems = (): HTMLElement[] =>
        Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? []);

    useEffect(() => {
        if (ready) enabledItems()[0]?.focus();
    }, [ready]);

    /** Handles Escape, Up, Down, Home and End inside the menu. */
    const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            onEscape();
            return;
        }
        const items = enabledItems();
        if (items.length === 0) return;
        const currentIndex = items.indexOf(document.activeElement as HTMLElement);
        let nextIndex: number | null = null;
        if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
        else if (event.key === 'ArrowUp') nextIndex = currentIndex <= 0 ? items.length - 1 : currentIndex - 1;
        else if (event.key === 'Home') nextIndex = 0;
        else if (event.key === 'End') nextIndex = items.length - 1;
        if (nextIndex !== null) {
            event.preventDefault();
            items[nextIndex].focus();
        }
    };

    return (
        <div
            ref={menuRef}
            className="split-button__menu"
            role="menu"
            aria-label={menuLabel}
            style={{
                left: placement?.left ?? 0,
                top: placement?.top ?? 0,
                minWidth: placement?.minWidth,
                visibility: placement ? 'visible' : 'hidden',
            }}
            onKeyDown={handleKeyDown}
        >
            {options.map((option) => (
                <button
                    type="button"
                    key={option.id}
                    role="menuitem"
                    className="split-button__item"
                    aria-disabled={option.disabled ? true : undefined}
                    title={option.title}
                    onClick={() => { if (!option.disabled) onPick(option.id); }}
                >
                    <span className="split-button__item-label">{option.label}</span>
                    {option.detail && <span className="split-button__item-detail">{option.detail}</span>}
                </button>
            ))}
        </div>
    );
};

/**
 * Shows the fused control: the main `Button` and the arrow that opens the menu. Picking an entry
 * calls `onSelect` and closes the menu. Escape closes it and puts focus back on the arrow. A press
 * outside closes it too. While `busy` or `disabled`, both parts are off and the menu cannot open.
 * The menu sits in a portal, under the control, or above it when there is no room below.
 *
 * @param props See {@link SplitButtonProps}.
 * @returns The split button.
 */
export const SplitButton: React.FC<SplitButtonProps> = ({
    label, onClick, options, onSelect, variant, size, disabled = false, busy = false, menuLabel, title,
    fill = false, className = '',
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const arrowRef = useRef<HTMLButtonElement>(null);
    const menuRef = useRef<HTMLDivElement>(null);
    const [isOpen, setIsOpen] = useState(false);
    const isOff = disabled || busy;
    const close = useCallback(() => setIsOpen(false), []);
    const position = useAnchoredMenu(isOpen, containerRef, menuRef, 'below', close);

    // A control that turns off while its menu is open (a job starts) closes the menu too.
    useEffect(() => {
        if (isOff) setIsOpen(false);
    }, [isOff]);

    return (
        <div ref={containerRef} className={`split-button${fill ? ' split-button--fill' : ''} ${className}`.trim()}>
            <Button
                variant={variant}
                size={size}
                fit={!fill}
                className="split-button__main"
                onClick={onClick}
                disabled={isOff}
                pending={busy}
                title={title}
            >
                {label}
            </Button>
            <Button
                ref={arrowRef}
                variant={variant}
                size={size}
                fit
                className="split-button__arrow"
                onClick={() => setIsOpen((previous) => !previous)}
                disabled={isOff}
                aria-label={menuLabel}
                aria-haspopup="menu"
                aria-expanded={isOpen}
                title={menuLabel}
            >
                <span aria-hidden="true">{isOpen ? '▲' : '▼'}</span>
            </Button>
            {isOpen && createPortal(
                <SplitButtonMenu
                    options={options}
                    menuLabel={menuLabel}
                    onPick={(id) => { close(); onSelect(id); }}
                    onEscape={() => { close(); arrowRef.current?.focus(); }}
                    ready={position !== null}
                    placement={position && { left: position.left, top: position.top, minWidth: position.anchorWidth }}
                    menuRef={menuRef}
                />,
                document.body,
            )}
        </div>
    );
};
