/**
 * @file test_themeTokenAndMotion.test.ts
 * @description Tests the shared page readers in `ui/common/utils`: the cached theme token reader
 * and the reduced-motion check.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearThemeTokens, readThemeToken, readThemeTokenNumber } from '../utils/themeToken';
import { prefersReducedMotion } from '../utils/reducedMotion';

describe('readThemeToken', () => {
    afterEach(() => {
        document.documentElement.style.removeProperty('--test-color');
        document.documentElement.style.removeProperty('--test-size');
        clearThemeTokens();
    });

    /** A missing token gives the fallback and is not kept, so a later read sees the value. */
    it('gives the fallback for a missing token and reads it again later', () => {
        expect(readThemeToken('--test-color', '#fff')).toBe('#fff');
        document.documentElement.style.setProperty('--test-color', 'rgb(1, 2, 3)');
        expect(readThemeToken('--test-color', '#fff')).toBe('rgb(1, 2, 3)');
    });

    /** A read value is kept until the cache is cleared. */
    it('keeps a value once read', () => {
        document.documentElement.style.setProperty('--test-color', 'rgb(1, 2, 3)');
        expect(readThemeToken('--test-color', '#fff')).toBe('rgb(1, 2, 3)');
        document.documentElement.style.setProperty('--test-color', 'rgb(4, 5, 6)');
        expect(readThemeToken('--test-color', '#fff')).toBe('rgb(1, 2, 3)');
        clearThemeTokens();
        expect(readThemeToken('--test-color', '#fff')).toBe('rgb(4, 5, 6)');
    });

    /** A size token reads as its number; a missing one gives the fallback. */
    it('reads a token as a number', () => {
        expect(readThemeTokenNumber('--test-size', 11)).toBe(11);
        document.documentElement.style.setProperty('--test-size', '13px');
        expect(readThemeTokenNumber('--test-size', 11)).toBe(13);
    });
});

describe('prefersReducedMotion', () => {
    afterEach(() => vi.unstubAllGlobals());

    /** It follows the system setting, and says no when the page cannot ask. */
    it('follows the reduce-motion setting', () => {
        const original = window.matchMedia;
        try {
            window.matchMedia = ((query: string) => ({ matches: query.includes('reduce') })) as unknown as typeof window.matchMedia;
            expect(prefersReducedMotion()).toBe(true);
            window.matchMedia = ((() => ({ matches: false })) as unknown) as typeof window.matchMedia;
            expect(prefersReducedMotion()).toBe(false);
            (window as { matchMedia?: unknown }).matchMedia = undefined;
            expect(prefersReducedMotion()).toBe(false);
        } finally {
            window.matchMedia = original;
        }
    });
});
