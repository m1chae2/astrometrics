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

vi.mock('../common/queries/useTargetListQuery', () => ({
    useTargetListQuery: vi.fn(() => ({ data: [], isLoading: false, error: null, refetch: vi.fn() })),
}));

vi.mock('../common/hooks/useToast', () => ({
    useToast: () => ({ show: vi.fn() }),
}));

import { useAstronomyListQuery } from '../common/queries/useAstronomyListQuery';

describe('useSpectrumList Filtering Suite', () => {
    /**
     * Test case verifying filtering when objects have explicit hasSpectra / hasPhotometry or nested data.
     */
    it('should filter stars by With Spectra and With Photometry options', () => {
        /**
         * Purpose: Ensures dropdown option 'With Spectra' and 'With Photometry'
         * correctly filter objects based on hasSpectra/has_spectra or nested arrays.
         */
        const mockStars: Spectrum[] = [
            {
                id: 'star-1',
                label: 'Star 1 (Spectra Only)',
                hasSpectra: true,
                hasPhotometry: false,
                spectraHistory: [{ timestamp: '2026-01-01', wavelengths: [5000], intensities: [1.0] }],
            },
            {
                id: 'star-2',
                label: 'Star 2 (Photometry Only)',
                has_spectra: false,
                has_photometry: true,
                lightCurve: { timestamps: ['2026-01-01'], magnitudes: [12.5], fluxes: [100.0] },
            },
            {
                id: 'star-3',
                label: 'Star 3 (Neither Flag but has spectrumData)',
                spectrumData: [[5000, 5010], [1.0, 0.9]],
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

        // Default filter: All
        expect(result.current.items).toHaveLength(4);

        // Filter: With Spectra
        act(() => {
            result.current.setFilterOption('With Spectra');
        });
        expect(result.current.items.map((i) => i.id)).toEqual(['star-1', 'star-3']);
        expect(result.current.items[0].hasSpectra).toBe(true);

        // Filter: With Photometry
        act(() => {
            result.current.setFilterOption('With Photometry');
        });
        expect(result.current.items.map((i) => i.id)).toEqual(['star-2']);
        expect(result.current.items[0].hasPhotometry).toBe(true);
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
});
