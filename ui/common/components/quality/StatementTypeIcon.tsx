/**
 * @fileoverview The icon that tells a statement's type apart.
 *
 * The icon is paired with the type word in the screen, so type is never shown
 * by color alone. The shapes differ: a triangle, a lightbulb, a check mark
 * in a circle (a result), a bar and a dashed circle. Severity changes the outline: `info` is drawn thin, the
 * others thick.
 */

import React from 'react';
import { Severity, StatementKind } from '../../types/backendTypes';

export interface StatementTypeIconProps {
    /** The statement's kind. */
    kind: StatementKind;
    /** The statement's severity, if it has one. */
    severity?: Severity | null;
}

/**
 * Draws the icon of a statement type as inline SVG. It is hidden from screen
 * readers because the type word beside it carries the meaning.
 * @param props The kind and severity.
 * @return The icon.
 */
export const StatementTypeIcon: React.FC<StatementTypeIconProps> = ({ kind, severity }) => {
    const strokeWidth = severity === Severity.INFO ? 1 : 2.5;
    let shape: React.ReactNode;
    switch (kind) {
        case StatementKind.WARNING:
            shape = (
                <>
                    <path d="M12 3 L22 20 H2 Z" />
                    <path d="M12 10 V15 M12 17.5 V18" />
                </>
            );
            break;
        case StatementKind.RECOMMENDATION:
            shape = (
                <>
                    <path d="M9 18 H15 M10 21 H14" />
                    <path d="M12 3 A6 6 0 0 0 8 13.5 C8.7 14.3 9 15 9 16 H15 C15 15 15.3 14.3 16 13.5 A6 6 0 0 0 12 3 Z" />
                </>
            );
            break;
        case StatementKind.RESULT:
            shape = (
                <>
                    <circle cx="12" cy="12" r="9" />
                    <path d="M8 12.5 L11 15.5 L16.5 9" />
                </>
            );
            break;
        case StatementKind.CONSTRAINT:
            shape = (
                <>
                    <path d="M4 12 H20" />
                    <path d="M7 6 V18 M17 6 V18" />
                </>
            );
            break;
        default:
            shape = (
                <>
                    <circle cx="12" cy="12" r="9" strokeDasharray="3 3" />
                    <path d="M12 8 V13 M12 16 V16.5" />
                </>
            );
    }
    return (
        <svg
            className={`statement-icon statement-icon--${kind}`}
            data-severity={severity ?? 'none'}
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
        >
            {shape}
        </svg>
    );
};
