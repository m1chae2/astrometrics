/**
 * @fileoverview Test suite for useStarsBySpectralClassList, the secondary
 * star list for the Astronomy Manager's "browse by spectral class"
 * navigation mode.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useStarsBySpectralClassList } from '../astronomyDisplay/hooks/useStarsBySpectralClassList';

vi.mock('../common/queries/useStarsBySpectralClassQuery', () => ({
    useStarsBySpectralClassQuery: vi.fn(),
}));

import { useStarsBySpectralClassQuery } from '../common/queries/useStarsBySpectralClassQuery';

describe('useStarsBySpectralClassList', () => {
    it('shows the catalog type and match quality, already sorted by the backend', () => {
        vi.mocked(useStarsBySpectralClassQuery).mockReturnValue({
            data: [
                {
                    id: 'HD 20630',
                    name: 'HD 20630',
                    magnitude: 4.83,
                    spectralType: 'G2V',
                    hasSpectra: true,
                    hasPhotometry: false,
                    selfDeterminedSpectralTypeRms: 0.04,
                },
                {
                    id: 'HD 190406',
                    name: 'HD 190406',
                    magnitude: 5.0,
                    spectralType: 'G1V',
                    hasSpectra: false,
                    hasPhotometry: false,
                    selfDeterminedSpectralTypeRms: null,
                },
            ],
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useStarsBySpectralClassList('G'));

        expect(result.current.items.map((i) => i.id)).toEqual(['HD 20630', 'HD 190406']);
        expect(result.current.items[0]).toMatchObject({ subtitle: 'G2V · 4% off', hasSpectra: true });
        expect(result.current.items[1]).toMatchObject({ subtitle: 'G1V · not yet matched', hasSpectra: false });
    });

    it('filters stars by name, case-insensitively', () => {
        vi.mocked(useStarsBySpectralClassQuery).mockReturnValue({
            data: [
                {
                    id: 'HD 20630',
                    name: 'HD 20630',
                    magnitude: 4.83,
                    spectralType: 'G2V',
                    hasSpectra: true,
                    hasPhotometry: false,
                    selfDeterminedSpectralTypeRms: 0.04,
                },
                {
                    id: 'HD 190406',
                    name: 'HD 190406',
                    magnitude: 5.0,
                    spectralType: 'G1V',
                    hasSpectra: false,
                    hasPhotometry: false,
                    selfDeterminedSpectralTypeRms: null,
                },
            ],
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useStarsBySpectralClassList('G'));

        act(() => {
            result.current.setFilterText('20630');
        });

        expect(result.current.items.map((i) => i.id)).toEqual(['HD 20630']);
    });
});
