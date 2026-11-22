/**
 * @fileoverview useAnchoredMenu: the shared placement and dismissal logic for a menu that is drawn
 * in a portal on `document.body` next to a control. `PopoverSelect` and `SplitButton` both use it.
 * It measures the control, puts the menu on the preferred side (or the other side when it does not
 * fit), keeps the menu inside the window, follows the control on scroll and resize, and calls
 * `onDismiss` for a press outside the control and the menu.
 */

import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react';

/** The gap in pixels between the control and the menu. */
const MENU_GAP_PX = 8;
/** The space in pixels kept between the menu and the window edge. */
const WINDOW_MARGIN_PX = 8;

/** Where the menu is drawn, in window pixels. */
export interface AnchoredMenuPosition {
    left: number;
    top: number;
    /** The control's width, for menus that match it. */
    anchorWidth: number;
}

/**
 * Places a portaled menu next to its control and closes it on an outside press.
 *
 * @param isOpen True while the menu is shown.
 * @param anchorRef The control the menu hangs from. Its bounding box sets the menu position.
 * @param menuRef The menu element. Its size decides whether it fits.
 * @param placement The preferred side: "below" or "above".
 * @param onDismiss Called for a press outside both the control and the menu.
 * @returns The menu position, or null until it is measured (keep the menu hidden until then).
 */
export function useAnchoredMenu(
    isOpen: boolean,
    anchorRef: RefObject<HTMLElement | null>,
    menuRef: RefObject<HTMLElement | null>,
    placement: 'below' | 'above',
    onDismiss: () => void,
): AnchoredMenuPosition | null {
    const [position, setPosition] = useState<AnchoredMenuPosition | null>(null);

    /** Places the menu next to the control: on the preferred side, or the other side when it does not fit. */
    const updatePosition = useCallback(() => {
        const anchor = anchorRef.current;
        if (!anchor) return;
        const rect = anchor.getBoundingClientRect();
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
        setPosition({ left, top, anchorWidth: rect.width });
    }, [anchorRef, menuRef, placement]);

    // Measure and place the menu before the browser paints it, so it never flashes in the wrong spot.
    useLayoutEffect(() => {
        if (isOpen) updatePosition();
        else setPosition(null);
    }, [isOpen, updatePosition]);

    // While open: follow the control on scroll or resize, and dismiss on a press outside the control and menu.
    useEffect(() => {
        if (!isOpen) return;
        const handlePress = (event: MouseEvent) => {
            const target = event.target as Node;
            if (!anchorRef.current?.contains(target) && !menuRef.current?.contains(target)) onDismiss();
        };
        document.addEventListener('mousedown', handlePress);
        window.addEventListener('resize', updatePosition);
        window.addEventListener('scroll', updatePosition, true);
        return () => {
            document.removeEventListener('mousedown', handlePress);
            window.removeEventListener('resize', updatePosition);
            window.removeEventListener('scroll', updatePosition, true);
        };
    }, [isOpen, anchorRef, menuRef, onDismiss, updatePosition]);

    return position;
}
