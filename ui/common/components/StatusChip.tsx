/**
 * @fileoverview StatusChip: a small pill with an icon and a word, so a status is never told by color alone.
 */

import React from 'react';
import './sharedComponents.css';

export type StatusChipStatus = 'ok' | 'warning' | 'error' | 'info' | 'neutral';

export interface StatusChipProps {
    status: StatusChipStatus;
    /** The word shown beside the icon, for example "Ready". */
    label: string;
}

/** Inline SVG icons, one shape per status so the shapes differ as well as the colors. */
const STATUS_ICON_PATHS: Record<StatusChipStatus, React.ReactNode> = {
    ok: <path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor" strokeWidth="2" />,
    warning: <path d="M8 2 15 14H1Z M8 6v4 M8 11.5v1" fill="none" stroke="currentColor" strokeWidth="1.6" />,
    error: <path d="M3.5 3.5 12.5 12.5 M12.5 3.5 3.5 12.5" fill="none" stroke="currentColor" strokeWidth="2" />,
    info: <path d="M8 7v5 M8 4v1" fill="none" stroke="currentColor" strokeWidth="2" />,
    neutral: <circle cx="8" cy="8" r="3" fill="currentColor" />,
};

/**
 * Shows a status chip (icon plus word).
 * @param props See {@link StatusChipProps}.
 * @return The chip element.
 */
export const StatusChip: React.FC<StatusChipProps> = ({ status, label }) => (
    <span className={`status-chip status-chip--${status}`} data-status={status}>
        <svg className="status-chip__icon" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            {STATUS_ICON_PATHS[status]}
        </svg>
        <span className="status-chip__word">{label}</span>
    </span>
);
