/**
 * @fileoverview InfoButton: a small "i" button that shows an explanation on hover, on keyboard
 * focus and on click, so touch and keyboard users can read it too.
 */

import React, { useId, useState } from 'react';
import './sharedComponents.css';

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

    return (
        <span
            className="info-button"
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
        >
            <button
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
            {isShown && (
                <span id={textId} role="note" className="info-button__text">{text}</span>
            )}
        </span>
    );
};
