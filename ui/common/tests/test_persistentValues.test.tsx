/**
 * @file test_persistentValues.test.tsx
 * @description Tests the saved states in `usePersistentFlag.ts`: a number kept inside its range,
 * a choice moved once from an older key, and a default when nothing usable is saved.
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { usePersistentFlag, usePersistentNumber } from '../hooks/usePersistentFlag';

describe('usePersistentNumber', () => {
    beforeEach(() => localStorage.clear());

    /** Nothing saved, or text that is not a number, gives the default. */
    it('starts at the default when nothing usable is saved', () => {
        expect(renderHook(() => usePersistentNumber('test:opacity', 0.8)).result.current[0]).toBe(0.8);
        localStorage.setItem('test:opacity', 'half');
        expect(renderHook(() => usePersistentNumber('test:opacity', 0.8)).result.current[0]).toBe(0.8);
    });

    /** A set value is kept inside the range and saved; the next start reads it back. */
    it('keeps a set value inside the range and saves it', () => {
        const { result } = renderHook(() => usePersistentNumber('test:opacity', 0.8, { min: 0.05, max: 1 }));
        act(() => result.current[1](0));
        expect(result.current[0]).toBe(0.05);
        expect(localStorage.getItem('test:opacity')).toBe('0.05');
        act(() => result.current[1](0.4));
        expect(renderHook(() => usePersistentNumber('test:opacity', 0.8, { min: 0.05, max: 1 })).result.current[0]).toBe(0.4);
    });

    /** A saved value outside the range is brought inside it. */
    it('keeps a saved value inside the range', () => {
        localStorage.setItem('test:opacity', '7');
        expect(renderHook(() => usePersistentNumber('test:opacity', 0.8, { max: 1 })).result.current[0]).toBe(1);
    });
});

describe('usePersistentFlag with an older key', () => {
    beforeEach(() => localStorage.clear());

    /** A choice saved under the older key is read once, moved to the new key, and the old key removed. */
    it('moves the saved choice from the older key', () => {
        localStorage.setItem('old.key', 'true');
        const { result } = renderHook(() => usePersistentFlag('astrometrics:test:flag', false, { legacyKey: 'old.key' }));
        expect(result.current[0]).toBe(true);
        expect(localStorage.getItem('astrometrics:test:flag')).toBe('true');
        expect(localStorage.getItem('old.key')).toBeNull();
    });

    /** The new key wins when both are saved. */
    it('prefers the new key', () => {
        localStorage.setItem('old.key', 'true');
        localStorage.setItem('astrometrics:test:flag', 'false');
        const { result } = renderHook(() => usePersistentFlag('astrometrics:test:flag', true, { legacyKey: 'old.key' }));
        expect(result.current[0]).toBe(false);
    });
});
