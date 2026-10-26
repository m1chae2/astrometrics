/**
 * @fileoverview The planning time shared by the planning displays.
 *
 * The planning time is the moment the Observation Planner and the Planetarium
 * work at. `null` means Now. It follows the backend's handoff state, so every
 * window shows the same time. Setting it sends it to the backend with the
 * local UTC offset; `returnToNow` sends a clear request. Observation Execution
 * always works at Now and does not use it.
 */

import React, { createContext, useCallback, useContext, useMemo, useState, ReactNode } from 'react';
import { useOptionalHandoff } from './HandoffContext';

/** What `usePlanningTime` returns. */
export interface PlanningTimeContextValue {
    /** ISO 8601 text with a UTC offset, or `null` for Now. */
    planningTime: string | null;
    /** Fixes the planning time at a moment (a `Date`, or ISO 8601 text that carries an offset). */
    setPlanningTime: (moment: Date | string) => void;
    /** Goes back to working at Now. */
    returnToNow: () => void;
}

const PlanningTimeContext = createContext<PlanningTimeContextValue | undefined>(undefined);

/**
 * Writes a moment as ISO 8601 text with the local UTC offset (for example `2026-10-24T22:00:00-06:00`).
 * @param moment The moment to write.
 * @return The text. The backend refuses text with no offset, so this always adds one.
 */
export function toIsoWithLocalOffset(moment: Date): string {
    const pad = (value: number, width = 2): string => String(Math.trunc(Math.abs(value))).padStart(width, '0');
    const offsetMinutes = -moment.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? '+' : '-';
    return (
        `${pad(moment.getFullYear(), 4)}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}` +
        `T${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}` +
        `${sign}${pad(offsetMinutes / 60)}:${pad(offsetMinutes % 60)}`
    );
}

/**
 * Provides the shared planning time. Inside a `HandoffProvider` it follows the
 * backend; outside one (an isolated test) it keeps the time in this window only.
 * @param props The children.
 * @return The children inside the planning time context.
 */
export const PlanningTimeProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const handoff = useOptionalHandoff();
    const [localPlanningTime, setLocalPlanningTime] = useState<string | null>(null);
    const publish = handoff?.publish;

    const planningTime = handoff ? (handoff.state?.planning_time ?? null) : localPlanningTime;

    const setPlanningTime = useCallback(
        (moment: Date | string) => {
            const text = typeof moment === 'string' ? moment : toIsoWithLocalOffset(moment);
            if (publish) void publish({ planning_time: text });
            else setLocalPlanningTime(text);
        },
        [publish],
    );

    const returnToNow = useCallback(() => {
        if (publish) void publish({ clear_planning_time: true });
        else setLocalPlanningTime(null);
    }, [publish]);

    const value = useMemo(
        () => ({ planningTime, setPlanningTime, returnToNow }),
        [planningTime, setPlanningTime, returnToNow],
    );
    return <PlanningTimeContext.Provider value={value}>{children}</PlanningTimeContext.Provider>;
};

/**
 * Reads the shared planning time.
 * @return The planning time and the two ways to change it.
 * @throws Error when used outside a `PlanningTimeProvider`.
 */
export const usePlanningTime = (): PlanningTimeContextValue => {
    const context = useContext(PlanningTimeContext);
    if (context === undefined) {
        throw new Error('usePlanningTime must be used within a PlanningTimeProvider');
    }
    return context;
};
