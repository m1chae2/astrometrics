/**
 * @fileoverview Shows the planning time: "Now", or "fixed at 22:00" with the
 * date when the time is not today, and a "Back to Now" button while it is fixed.
 * This only formats the shared planning time for display; it decides nothing.
 */

import React from 'react';
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

/**
 * The planning time indicator.
 * @return The indicator with its "Back to Now" button.
 */
export const PlanningTimeIndicator: React.FC = () => {
    const { planningTime, returnToNow } = usePlanningTime();
    return (
        <span className="planning-time-indicator" data-testid="planning-time-indicator">
            <span className="planning-time-indicator__label">
                Planning time: {planningTime === null ? 'Now' : `fixed at ${formatPlanningTime(planningTime)}`}
            </span>
            {planningTime !== null && (
                <button type="button" className="planning-time-indicator__button" onClick={returnToNow}>
                    Back to Now
                </button>
            )}
        </span>
    );
};
