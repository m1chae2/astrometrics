/**
 * @fileoverview Test suite validating the filtering logic and badge calculation
 * in useSpectrumList hook for stars with spectroscopy and photometry data.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useSpectrumList } from '../astronomyManager/hooks/useSpectrumList';
import { Spectrum } from '../common/types/backendTypes';

// Mock TanStack Query hooks and toast hook
vi.mock('../common/queries/useAstronomyListQuery', () => ({
    useAstronomyListQuery: vi.fn(),
}));

vi.mock('../common/queries/useAstronomyCountQuery', () => ({
    useAstronomyCountQuery: () => ({ data: undefined }),
}));

vi.mock('../common/hooks/useToast', () => ({
    useToast: () => ({ show: vi.fn() }),
}));

import { useAstronomyListQuery } from '../common/queries/useAstronomyListQuery';

describe('useSpectrumList Filtering Suite', () => {
    /**
     * Test case verifying each star's own hasSpectra/hasPhotometry badge is
     * computed from its explicit flags or nested spectroscopy/photometry data.
     */
    it('computes each star\'s hasSpectra/hasPhotometry badge from its own data', () => {
        const mockStars: Spectrum[] = [
            {
                id: 'star-1',
                label: 'Star 1 (Spectra Only)',
                hasSpectra: true,
                hasPhotometry: false,
            },
            {
                id: 'star-2',
                label: 'Star 2 (Photometry Only)',
                has_spectra: false,
                has_photometry: true,
                photometry: { timestamps: ['2026-01-01'], magnitudes: [12.5], fluxes: [100.0] },
            },
            {
                id: 'star-3',
                label: 'Star 3 (Neither Flag but has spectroscopy data)',
                spectroscopy: { wavelengthsAngstrom: [5000, 5010], intensities: [1.0, 0.9] },
            },
            {
                id: 'star-4',
                label: 'Star 4 (No Data)',
            },
        ];

        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data: mockStars,
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);

        const { result } = renderHook(() => useSpectrumList());

        expect(result.current.items).toHaveLength(4);
        const byId = Object.fromEntries(result.current.items.map((i) => [i.id, i]));
        expect(byId['star-1']).toMatchObject({ hasSpectra: true, hasPhotometry: false });
        expect(byId['star-2']).toMatchObject({ hasSpectra: false, hasPhotometry: true });
        expect(byId['star-3']).toMatchObject({ hasSpectra: true, hasPhotometry: false });
        expect(byId['star-4']).toMatchObject({ hasSpectra: false, hasPhotometry: false });
    });

    /**
     * Test case verifying each row carries the star's magnitude, the library's catalog-magnitude
     * verdict and its spectral type, so a card opened from the row can show the same values.
     */
    it('carries magnitude, catalog verdict and spectral type on each row', () => {
        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data: [
                { id: 'Albireo', name: 'Albireo', ra: 292.68, dec: 27.96, magnitude: 3.08, hasCatalogMagnitude: true, spectralType: 'K3II+B9.5V' },
                { id: 'HD 1', name: 'HD 1', ra: 1, dec: 2, magnitude: 0, hasCatalogMagnitude: false, spectralType: 'Unknown' },
            ] as Spectrum[],
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);

        const { result } = renderHook(() => useSpectrumList());

        const byId = Object.fromEntries(result.current.items.map((i) => [i.id, i]));
        expect(byId['Albireo']).toMatchObject({ magnitude: 3.08, hasCatalogMagnitude: true, spectralType: 'K3II+B9.5V' });
        expect(byId['HD 1'].hasCatalogMagnitude).toBe(false);
        expect(byId['HD 1'].spectralType).toBeUndefined();
    });

    /**
     * Test case verifying that useSpectrumList caps rendered items at 100.
     */
    it('should cap rendered items to 100 when more than 100 stars are returned', () => {
        /**
         * Purpose: Ensures that even if more than 100 items are present in data,
         * the UI only maps and renders at most 100 items at a time.
         */
        const manyStars: Spectrum[] = Array.from({ length: 150 }, (_, i) => ({
            id: `hd-${i}`,
            label: `HD ${i}`,
            name: `HD ${i}`,
        }));

        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data: manyStars,
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);

        const { result } = renderHook(() => useSpectrumList());

        expect(result.current.items).toHaveLength(100);
        expect(result.current.items[0].id).toBe('hd-0');
        expect(result.current.items[99].id).toBe('hd-99');
    });

    /**
     * Test case verifying that useAstronomyListQuery is called with limit=100 and search options.
     */
    it('should pass search filter and limit 100 to useAstronomyListQuery', () => {
        /**
         * Purpose: Ensures useSpectrumList provides query options with limit: 100
         * and debounces search input for backend querying.
         */
        vi.useFakeTimers();

        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data: [],
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);

        const { result } = renderHook(() => useSpectrumList());

        // Initial call should have limit: 100
        expect(useAstronomyListQuery).toHaveBeenCalledWith(
            expect.objectContaining({ limit: 100, search: '' })
        );

        // Update search filter text
        act(() => {
            result.current.setFilterText('Polaris');
        });

        // Fast-forward timers for debounce (250ms)
        act(() => {
            vi.advanceTimersByTime(300);
        });

        expect(useAstronomyListQuery).toHaveBeenLastCalledWith(
            expect.objectContaining({ limit: 100, search: 'Polaris' })
        );

        vi.useRealTimers();
    });

    /**
     * Test case verifying pagination state, offset calculation, and page switching.
     */
    it('should support pagination and calculate offset properly', () => {
        /**
         * Purpose: Ensures useSpectrumList provides pagination controls (nextPage, prevPage, setPage)
         * and properly calculates offset for query options.
         */
        const manyStars: Spectrum[] = Array.from({ length: 100 }, (_, i) => ({
            id: `star-${i}`,
            label: `Star ${i}`,
        }));

        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data: manyStars,
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);

        const { result, rerender } = renderHook(() => useSpectrumList());

        expect(result.current.page).toBe(1);
        expect(result.current.hasMore).toBe(true);
        expect(result.current.hasPrevious).toBe(false);
        expect(useAstronomyListQuery).toHaveBeenCalledWith(
            expect.objectContaining({ limit: 100, offset: 0 })
        );

        // Advance to next page
        act(() => {
            result.current.nextPage();
        });
        rerender();

        expect(result.current.page).toBe(2);
        expect(result.current.hasPrevious).toBe(true);
        expect(useAstronomyListQuery).toHaveBeenCalledWith(
            expect.objectContaining({ limit: 100, offset: 100 })
        );

        // Go back to previous page
        act(() => {
            result.current.prevPage();
        });
        rerender();

        expect(result.current.page).toBe(1);
        expect(result.current.hasPrevious).toBe(false);
        expect(useAstronomyListQuery).toHaveBeenCalledWith(
            expect.objectContaining({ limit: 100, offset: 0 })
        );
    });
});

describe('useSpectrumList default star', () => {
    /** Mocks the list query to return the given stars, already loaded. */
    const mockStars = (data: Spectrum[]) => {
        vi.mocked(useAstronomyListQuery).mockReturnValue({
            data,
            isLoading: false,
            error: null,
            refetch: vi.fn(),
        } as any);
    };

    /** A star with a spectrum is chosen over a photometry-only star and over the others. */
    it('chooses a spectrum star over a photometry-only star over the rest', () => {
        mockStars([
            { id: 'plain', name: 'plain' },
            { id: 'light-curve', name: 'light-curve', has_photometry: true },
            { id: 'with-spectrum', name: 'with-spectrum', has_spectra: true },
        ] as Spectrum[]);
        const setPendingId = vi.fn();
        renderHook(() => useSpectrumList('Albireo', undefined, '', '', setPendingId));
        expect(setPendingId).toHaveBeenCalledTimes(1);
        expect(setPendingId).toHaveBeenCalledWith('with-spectrum');
    });

    /** With no spectrum star, the photometry star is chosen; with neither, the first star. */
    it('falls back to a photometry star, then to the first star', () => {
        mockStars([
            { id: 'plain', name: 'plain' },
            { id: 'light-curve', name: 'light-curve', has_photometry: true },
        ] as Spectrum[]);
        const withPhotometry = vi.fn();
        renderHook(() => useSpectrumList('Albireo', undefined, '', '', withPhotometry));
        expect(withPhotometry).toHaveBeenCalledWith('light-curve');

        mockStars([{ id: 'first', name: 'first' }, { id: 'second', name: 'second' }] as Spectrum[]);
        const withNeither = vi.fn();
        renderHook(() => useSpectrumList('Vega', undefined, '', '', withNeither));
        expect(withNeither).toHaveBeenCalledWith('first');
    });

    /** A star that is already selected when the panel opens (a hand-off) is kept. */
    it('keeps a star that was already selected on first open', () => {
        mockStars([{ id: 'with-spectrum', name: 'with-spectrum', has_spectra: true }] as Spectrum[]);
        const setPendingId = vi.fn();
        renderHook(() => useSpectrumList('Albireo', undefined, '', 'handed-off', setPendingId));
        expect(setPendingId).not.toHaveBeenCalled();
    });

    /** A star the user picked in a target is chosen again when the user returns to that target. */
    it('remembers the user pick for a target and replaces the star on a target change', () => {
        mockStars([
            { id: 'a-spectrum', name: 'a-spectrum', has_spectra: true },
            { id: 'a-other', name: 'a-other' },
        ] as Spectrum[]);
        const setPendingId = vi.fn();
        const { result, rerender } = renderHook(
            ({ target }) => useSpectrumList(target, undefined, '', '', setPendingId),
            { initialProps: { target: 'Albireo' } }
        );
        expect(setPendingId).toHaveBeenLastCalledWith('a-spectrum');
        act(() => result.current.selectStar('a-other'));
        expect(setPendingId).toHaveBeenLastCalledWith('a-other');

        mockStars([{ id: 'b-first', name: 'b-first' }] as Spectrum[]);
        rerender({ target: 'Vega' });
        expect(setPendingId).toHaveBeenLastCalledWith('b-first');

        mockStars([
            { id: 'a-spectrum', name: 'a-spectrum', has_spectra: true },
            { id: 'a-other', name: 'a-other' },
        ] as Spectrum[]);
        rerender({ target: 'Albireo' });
        expect(setPendingId).toHaveBeenLastCalledWith('a-other');
    });

    /** A target with no stars selects nothing, so the list shows its empty message. */
    it('selects nothing for a target with no stars', () => {
        mockStars([]);
        const setPendingId = vi.fn();
        const { result } = renderHook(() => useSpectrumList('Empty', undefined, '', '', setPendingId));
        expect(result.current.items).toHaveLength(0);
        expect(setPendingId).not.toHaveBeenCalled();
    });
});
