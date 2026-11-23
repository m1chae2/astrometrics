/**
 * @fileoverview StatusChip: a Badge with an icon and a word, so a status is never told by color alone.
 */

import React, { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Badge } from './Badge';

export type StatusChipStatus = 'ok' | 'warning' | 'error' | 'info' | 'neutral';

export interface StatusChipProps {
    status: StatusChipStatus;
    /** The word shown beside the icon, for example "Ready". */
    label: string;
    /**
     * A shorter word for narrow windows, for example "SIM". The full label stays in the tooltip and the
     * accessible name, so the chip is never hidden.
     */
    shortLabel?: string;
    /** The tooltip. With `shortLabel` it defaults to the full label. */
    title?: string;
    /**
     * Draws only the icon, as a small round chip for tight lists. The word stays in the page for screen readers
     * and in the tooltip.
     */
    compact?: boolean;
    /**
     * Rich content (a table, a list) shown in a popover under the chip while the pointer is over it or
     * the chip has keyboard focus. The chip becomes focusable; Escape hides the popover. The chip's
     * word and status stay the only status on the screen; the details explain it.
     */
    details?: React.ReactNode;
}

/** Gap in pixels between the chip and its details popover. */
const DETAILS_GAP_PX = 4;
/** Closest the details popover may come to the window edge, in pixels. */
const DETAILS_EDGE_MARGIN_PX = 8;

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
export const StatusChip: React.FC<StatusChipProps> = ({
    status, label, shortLabel, title, compact = false, details,
}) => {
    const chip = (
    <Badge
        title={details ? title : (title ?? (shortLabel || compact ? label : undefined))}
        {...(shortLabel ? { role: 'img', 'aria-label': label } : {})}
        tone={status}
        className={`status-chip status-chip--${status}${compact ? ' status-chip--compact' : ''}`}
        data-status={status}
        icon={(
            <svg className="badge__icon status-chip__icon" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                {STATUS_ICON_PATHS[status]}
            </svg>
        )}
    >
        {shortLabel ? (
            <>
                <span className="status-chip__word status-chip__word--full" aria-hidden="true">{label}</span>
                <span className="status-chip__word status-chip__word--short" aria-hidden="true">{shortLabel}</span>
            </>
        ) : (
            <span className="status-chip__word">{label}</span>
        )}
    </Badge>
    );
    return details ? <StatusChipDetails chip={chip}>{details}</StatusChipDetails> : chip;
};

/**
 * Wraps a chip so that hovering or focusing it shows rich content in a popover on the page body, so
 * no panel clips it. The wrapper is focusable and points to the popover with `aria-describedby`.
 * @param props The chip and its details.
 * @return The wrapped chip.
 */
const StatusChipDetails: React.FC<{ chip: React.ReactNode; children: React.ReactNode }> = ({ chip, children }) => {
    const detailsId = useId();
    const [isHovered, setIsHovered] = useState(false);
    const [isFocused, setIsFocused] = useState(false);
    const isShown = isHovered || isFocused;
    const triggerRef = useRef<HTMLSpanElement>(null);
    const popoverRef = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState<{ top: number; left: number; maxHeight: number } | null>(null);

    // Place the popover under the chip, left edges lined up. It shifts left to stay inside the window,
    // flips above the chip when there is more room there, and scrolls when it is taller than the room.
    // It follows the chip on scroll or resize.
    useLayoutEffect(() => {
        if (!isShown) {
            setPosition(null);
            return undefined;
        }
        const place = () => {
            const rect = triggerRef.current?.getBoundingClientRect();
            if (!rect) return;
            const popover = popoverRef.current;
            const width = popover?.offsetWidth ?? 0;
            const height = popover?.scrollHeight ?? 0;
            const roomBelow = window.innerHeight - rect.bottom - DETAILS_GAP_PX - DETAILS_EDGE_MARGIN_PX;
            const roomAbove = rect.top - DETAILS_GAP_PX - DETAILS_EDGE_MARGIN_PX;
            const isAbove = height > roomBelow && roomAbove > roomBelow;
            const maxHeight = Math.max(0, isAbove ? roomAbove : roomBelow);
            const shownHeight = Math.min(height, maxHeight);
            const top = isAbove ? rect.top - DETAILS_GAP_PX - shownHeight : rect.bottom + DETAILS_GAP_PX;
            const maxLeft = Math.max(DETAILS_EDGE_MARGIN_PX, window.innerWidth - width - DETAILS_EDGE_MARGIN_PX);
            const left = Math.min(Math.max(DETAILS_EDGE_MARGIN_PX, rect.left), maxLeft);
            setPosition({ top, left, maxHeight });
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
            ref={triggerRef}
            className="status-chip__details-trigger"
            tabIndex={0}
            aria-describedby={isShown ? detailsId : undefined}
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onKeyDown={(event) => {
                if (event.key === 'Escape') {
                    setIsHovered(false);
                    setIsFocused(false);
                }
            }}
        >
            {chip}
            {isShown && createPortal(
                <div
                    id={detailsId}
                    ref={popoverRef}
                    role="tooltip"
                    className="status-chip__details"
                    style={position ?? { visibility: 'hidden', top: 0, left: 0 }}
                >
                    {children}
                </div>,
                document.body,
            )}
        </span>
    );
};
