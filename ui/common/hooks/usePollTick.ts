/**
 * @fileoverview Shared "tick on an interval" hook for polling hooks.
 *
 * Several hooks across the UI (target status, calibration stats, target
 * visibility, remote-target ingestion scanning) each hand-rolled their own
 * `setInterval` + cleanup for "re-fetch every N seconds," slightly
 * differently each time. This centralizes the interval itself: pair the
 * returned tick counter with `useBackendFetch`'s dependency list to get a
 * polled fetch with proper cancellation on every tick.
 */

import { useEffect, useState } from 'react';

/**
 * Returns a counter that increments every `intervalMs` milliseconds.
 *
 * Use as a dependency to `useBackendFetch` (or any effect) to have it
 * re-run on a fixed interval, e.g.:
 * ```ts
 * const pollTick = usePollTick(10000);
 * const { data } = useBackendFetch((signal) => fetchThing(signal), [pollTick]);
 * ```
 *
 * @param intervalMs How often to tick, in milliseconds. Pass 0 or undefined
 *   to disable polling (the counter never advances) without a separate
 *   `enabled` flag at each call site.
 */
export function usePollTick(intervalMs?: number): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!intervalMs) return;
    const intervalId = setInterval(() => setTick(t => t + 1), intervalMs);
    return () => clearInterval(intervalId);
  }, [intervalMs]);

  return tick;
}
