/**
 * @fileoverview Test suite for useSpectralClassBrowserItems, the "browse by
 * spectral class" primary list used in the Astronomy Manager's split
 * target/star navigation.
 */

import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useSpectralClassBrowserItems } from '../astronomyDisplay/hooks/useSpectralClassBrowserItems';

vi.mock('../common/queries/useSpectralClassSummaryQuery', () => ({
    useSpectralClassSummaryQuery: vi.fn(),
}));

import { useSpectralClassSummaryQuery } from '../common/queries/useSpectralClassSummaryQuery';

describe('useSpectralClassBrowserItems', () => {
    it('leads with All, then each class showing its label and star count', () => {
        vi.mocked(useSpectralClassSummaryQuery).mockReturnValue({
            data: [
                { spectralClass: 'G', label: 'Yellow dwarfs', count: 9 },
                { spectralClass: 'M', label: 'Red dwarfs', count: 22 },
            ],
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useSpectralClassBrowserItems());

        expect(result.current.items.map((i) => i.id)).toEqual(['All', 'G', 'M']);
        const byId = Object.fromEntries(result.current.items.map((i) => [i.id, i]));
        expect(byId['G']).toMatchObject({ label: 'G — Yellow dwarfs', subtitle: '9 stars' });
        expect(byId['M']).toMatchObject({ label: 'M — Red dwarfs', subtitle: '22 stars' });
    });

    it('filters classes by letter or label, case-insensitively', () => {
        vi.mocked(useSpectralClassSummaryQuery).mockReturnValue({
            data: [
                { spectralClass: 'G', label: 'Yellow dwarfs', count: 9 },
                { spectralClass: 'M', label: 'Red dwarfs', count: 22 },
            ],
            isLoading: false,
            error: null,
        } as any);

        const { result } = renderHook(() => useSpectralClassBrowserItems());

        act(() => {
            result.current.setFilterText('red');
        });

        expect(result.current.items.map((i) => i.id)).toEqual(['All', 'M']);
    });
});
