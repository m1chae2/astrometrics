/**
 * @fileoverview Badge: a small tinted label. One component gives every badge in the app the same
 * fill, edge, corners and text size; the tone only picks the color.
 */

import React from 'react';
import { Button } from './Button';
import './Badge.css';

/** The color of a badge. `ok` is green, `warning` amber, `error` red, `info` and `accent` blue. */
export type BadgeTone = 'ok' | 'warning' | 'error' | 'info' | 'neutral' | 'accent' | 'purple';

export interface BadgeProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'onClick'> {
    /** The color. Leave it out for neutral grey. */
    tone?: BadgeTone;
    /** An icon drawn before the text, so the tone is not the only sign. It should carry `aria-hidden`. */
    icon?: React.ReactNode;
    /** Makes the badge a button that opens something. Without it the badge is plain text. */
    onClick?: React.MouseEventHandler<HTMLButtonElement>;
    /** Draws the edge dashed or dotted, for readers who cannot tell the tints apart. */
    edge?: 'solid' | 'dashed' | 'dotted';
    /** The text of the badge. */
    children?: React.ReactNode;
}

/**
 * Shows a badge. With `onClick` it is a native button, so it keeps keyboard access.
 * @param props See {@link BadgeProps}. Other props (such as `title`) go to the element.
 * @return The badge element.
 */
export const Badge: React.FC<BadgeProps> = ({
    tone = 'neutral', icon, onClick, edge = 'solid', className = '', children, ...elementProps
}) => {
    const classes = [
        'badge', `badge--${tone}`,
        edge !== 'solid' ? `badge--${edge}` : '',
        onClick ? 'badge--clickable' : '',
        className,
    ].filter(Boolean).join(' ');
    const content = (<>{icon}{children}</>);
    if (onClick) {
        return (
            <Button
                {...(elementProps as React.ButtonHTMLAttributes<HTMLButtonElement>)}
                variant="ghost"
                className={classes}
                onClick={onClick}
            >
                {content}
            </Button>
        );
    }
    return <span {...elementProps} className={classes}>{content}</span>;
};
