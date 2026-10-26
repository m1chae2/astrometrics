/**
 * @fileoverview InfoButton: a small "i" button that shows an explanation on hover, on keyboard
 * focus and on click, so touch and keyboard users can read it too. The explanation is drawn on
 * the page body, placed under the button, so a panel that clips its contents cannot cut it off.
 */

import React, { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './sharedComponents.css';

/** Gap in pixels between the button and its explanation. */
const TEXT_GAP_PX = 4;
/** Closest the explanation may come to the window edge, in pixels. */
const EDGE_MARGIN_PX = 8;

export interface InfoButtonProps {
    /** The explanation to show. */
    text: string;
    /** Accessible name of the button. Defaults to "More information". */
    label?: string;
}

/**
 * Shows an info button with its explanation text.
 *
 * The text is visible while the pointer is over the button, while the button has focus,
 * or after a click (click again, press Escape, or move focus away to hide it).
 * The button points to the text with `aria-describedby`.
 *
 * @param props See {@link InfoButtonProps}.
 * @return The info button element.
 */
export const InfoButton: React.FC<InfoButtonProps> = ({ text, label = 'More information' }) => {
    const textId = useId();
    const [isHovered, setIsHovered] = useState(false);
    const [isFocused, setIsFocused] = useState(false);
    const [isPinned, setIsPinned] = useState(false);
    const isShown = isHovered || isFocused || isPinned;
    const triggerRef = useRef<HTMLButtonElement>(null);
    const [position, setPosition] = useState<{ top: number; right: number } | null>(null);

    // Place the explanation under the button, right edges lined up, and follow the button while
    // the page scrolls or the window is resized.
    useLayoutEffect(() => {
        if (!isShown) {
            setPosition(null);
            return undefined;
        }
        const place = () => {
            const rect = triggerRef.current?.getBoundingClientRect();
            if (!rect) return;
            setPosition({
                top: rect.bottom + TEXT_GAP_PX,
                right: Math.max(EDGE_MARGIN_PX, window.innerWidth - rect.right),
            });
        };
        place();
        window.addEventListener('scroll', place, true);
        window.addEventListener('resize', place);
        return () => {
            window.removeEventListener('scroll', place, true);
            window.removeEventListener('resize', place);
        };
    }, [isShown]);

    return (
        <span
            className="info-button"
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
        >
            <button
                ref={triggerRef}
                type="button"
                className="info-button__trigger"
                aria-label={label}
                aria-expanded={isShown}
                aria-describedby={isShown ? textId : undefined}
                onClick={() => setIsPinned((previous) => !previous)}
                onFocus={() => setIsFocused(true)}
                onBlur={() => { setIsFocused(false); setIsPinned(false); }}
                onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                        setIsPinned(false);
                        setIsHovered(false);
                        setIsFocused(false);
                    }
                }}
            >
                <span aria-hidden="true">i</span>
            </button>
            {isShown && createPortal(
                <span
                    id={textId}
                    role="note"
                    className="info-button__text"
                    style={position ? { top: position.top, right: position.right } : { visibility: 'hidden' }}
                >
                    {text}
                </span>,
                document.body,
            )}
        </span>
    );
};
