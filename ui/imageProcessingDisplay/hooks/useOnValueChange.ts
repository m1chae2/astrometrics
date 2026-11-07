/**
 * @file useOnValueChange.ts
 * @description Runs a function only when a value really changes.
 * A hidden display is paused, and showing it again starts its effects over. An effect
 * that clears the user's work (the picked frame, an analysis result, a log) must not
 * run again just because the display came back, so it uses this hook instead.
 */
import { useEffect, useRef } from 'react';

/** Marks "nothing seen yet", so the function also runs for the first value. */
const NOT_SEEN = Symbol('not-seen');

/**
 * Calls `onChange` when `value` differs from the value seen last time. The last value
 * is kept in a ref, which survives a pause, so showing the display again does not call it.
 *
 * @param value The value to watch, such as the selected target.
 * @param onChange What to do when the value changes. It may return a clean-up function.
 */
export function useOnValueChange<T>(value: T, onChange: (value: T) => void | (() => void)): void {
    const lastValueRef = useRef<T | typeof NOT_SEEN>(NOT_SEEN);
    useEffect(() => {
        if (lastValueRef.current === value) return undefined;
        lastValueRef.current = value;
        return onChange(value);
        // Only the watched value decides when this runs.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);
}
