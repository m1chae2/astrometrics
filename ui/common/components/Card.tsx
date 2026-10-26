/**
 * @fileoverview Card: a bordered box with an optional title bar, used for list items such as the
 * Observation Manager's imaging cards. Supports a selected state and a current state.
 */

import React from 'react';
import './sharedComponents.css';

export interface CardProps {
    /** Title shown in the header bar. Leave out for a card with no header. */
    title?: React.ReactNode;
    /** Extra content on the right side of the header (counts, buttons). */
    headerContent?: React.ReactNode;
    /** True when the user picked this card. Draws the blue outline. */
    selected?: boolean;
    /** True when this card is the one in use right now. Sets `aria-current` and a thick left edge. */
    current?: boolean;
    /** Called when the card is clicked or activated with Enter or Space. Makes the card focusable. */
    onClick?: () => void;
    /** Accessible name when the title is not plain text. */
    ariaLabel?: string;
    className?: string;
    children?: React.ReactNode;
}

/**
 * Shows a bordered card.
 *
 * The look comes from the imaging cards of the Observation Manager. A card
 * with an `onClick` handler is focusable and answers Enter and Space.
 *
 * @param props See {@link CardProps}.
 * @return The card element.
 */
export const Card: React.FC<CardProps> = ({
    title,
    headerContent,
    selected = false,
    current = false,
    onClick,
    ariaLabel,
    className = '',
    children,
}) => {
    const classNames = [
        'card',
        selected ? 'card--selected' : '',
        current ? 'card--current' : '',
        onClick ? 'card--interactive' : '',
        className,
    ].filter(Boolean).join(' ');

    /**
     * Activates the card from the keyboard.
     * @param event The key event.
     */
    const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (onClick && event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            onClick();
        }
    };

    return (
        <div
            className={classNames}
            aria-current={current ? 'true' : undefined}
            aria-label={ariaLabel}
            data-selected={selected ? 'true' : undefined}
            tabIndex={onClick ? 0 : undefined}
            role={onClick ? 'button' : undefined}
            onClick={onClick}
            onKeyDown={onClick ? handleKeyDown : undefined}
        >
            {(title || headerContent) && (
                <div className="card__header">
                    {onClick
                        ? <span className="card__title">{title}</span>
                        : <h3 className="card__title">{title}</h3>}
                    {headerContent}
                </div>
            )}
            <div className="card__body">{children}</div>
        </div>
    );
};
