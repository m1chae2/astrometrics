/**
 * @fileoverview Shows the planning time: "Now", or "fixed at 22:00" with the
 * date when the time is not today, and a "Back to Now" button while it is fixed.
 * This only formats the shared planning time for display; it decides nothing.
 */

import React from 'react';
import { Button } from './Button';
import { usePlanningTime } from '../context/PlanningTimeContext';
import './planningTimeIndicator.css';

/**
 * Writes a planning time for display: clock time, plus the date when it is not today.
 * @param planningTime ISO 8601 text with a UTC offset.
 * @param now The current moment, for deciding whether to show the date.
 * @return Text such as `22:00` or `Oct 25, 22:00`.
 */
export function formatPlanningTime(planningTime: string, now: Date = new Date()): string {
    const moment = new Date(planningTime);
    if (Number.isNaN(moment.getTime())) return planningTime;
    const clock = moment.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    if (moment.toDateString() === now.toDateString()) return clock;
    return `${moment.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${clock}`;
}

/** Props for PlanningTimeIndicator. */
export interface PlanningTimeIndicatorProps {
    /**
     * "regular" (the default) reads "Planning time: Now". "compact" is for a
     * toolbar: a small muted "Planning time" label, then the value, in the
     * toolbar's small text.
     */
    size?: 'regular' | 'compact';
}

/**
 * The planning time indicator.
 * @param props The size.
 * @return The indicator with its "Back to Now" button.
 */
export const PlanningTimeIndicator: React.FC<PlanningTimeIndicatorProps> = ({ size = 'regular' }) => {
    const { planningTime, returnToNow } = usePlanningTime();
    const value = planningTime === null ? 'Now' : `fixed at ${formatPlanningTime(planningTime)}`;
    const isCompact = size === 'compact';
    return (
        <span
            className={`planning-time-indicator${isCompact ? ' planning-time-indicator--compact' : ''}`}
            data-testid="planning-time-indicator"
        >
            {isCompact ? (
                <>
                    <span className="planning-time-indicator__name" aria-hidden="true">Planning time</span>
                    <span className="planning-time-indicator__label" aria-label={`Planning time: ${value}`}>{value}</span>
                </>
            ) : (
                <span className="planning-time-indicator__label">Planning time: {value}</span>
            )}
            {planningTime !== null && (
                <Button variant="secondary" size="small" onClick={returnToNow}>
                    Back to Now
                </Button>
            )}
        </span>
    );
};
