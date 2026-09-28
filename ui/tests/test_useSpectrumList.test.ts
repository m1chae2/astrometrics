/**
 * @fileoverview Test suite validating the filtering logic and badge calculation
 * in useSpectrumList hook for stars with spectroscopy and photometry data.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useSpectrumList } from '../astronomyDisplay/hooks/useSpectrumList';
import { Spectrum } from '../common/types/backendTypes';

// Mock TanStack Query hooks and toast hook
vi.mock('../common/queries/useAstronomyListQuery', () => ({
    useAstronomyListQuery: vi.fn(),
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
