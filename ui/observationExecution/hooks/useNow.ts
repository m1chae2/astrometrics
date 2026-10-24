/**
 * @fileoverview A clock hook that re-renders its component on a fixed
 * interval, so lines such as "25 min ago" stay current. For display only.
 */

import { useEffect, useState } from 'react';

/**
 * Returns the current moment and refreshes it on an interval.
 * @param intervalMilliseconds How often to refresh. The default is one minute.
 * @return The current moment.
 */
export function useNow(intervalMilliseconds = 60_000): Date {
    const [now, setNow] = useState<Date>(() => new Date());
    useEffect(() => {
        const timer = window.setInterval(() => setNow(new Date()), intervalMilliseconds);
        return () => window.clearInterval(timer);
    }, [intervalMilliseconds]);
    return now;
}
