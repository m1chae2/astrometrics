/**
 * @fileoverview Tests that the catalog lists show an error with "Try again"
 * after a failed load, keep earlier items on screen, and expose retry.
 */

import React from 'react';
import { render, screen, fireEvent, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { useStarsBySpectralClassList } from '../astronomyManager/hooks/useStarsBySpectralClassList';

const service = vi.hoisted(() => ({ fetchStarsBySpectralClass: vi.fn() }));
vi.mock('../common/services/astronomyService', () => service);

vi.mock('../common/queries/catalogQueryPolicy', () => ({
    catalogQueryPolicy: (enabled: boolean) => ({ enabled, retry: false, staleTime: 0 }),
}));

vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
    unobserve() {}
});

const item = { id: 'a', value: 'a', label: 'Vega' };

describe('list error state', () => {
    it('shows the error and calls onRetry when the list is empty', () => {
        const onRetry = vi.fn();
        render(<RadioListManager items={[]} onSelect={() => {}} isError errorMessage="Could not load the stars." onRetry={onRetry} />);
        expect(screen.getByRole('alert').textContent).toContain('Could not load the stars.');
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it('keeps earlier items visible instead of the error', () => {
        render(<RadioListManager items={[item]} onSelect={() => {}} isError errorMessage="Could not load the stars." onRetry={() => {}} />);
        expect(screen.queryByRole('alert')).toBeNull();
        expect(screen.getByText('Vega')).toBeTruthy();
    });

    it('hook reports isError after the final retry and retry refetches', async () => {
        service.fetchStarsBySpectralClass.mockRejectedValue(new Error('busy'));
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrapper = ({ children }: { children: React.ReactNode }) => (
            <QueryClientProvider client={client}>{children}</QueryClientProvider>
        );
        const { result } = renderHook(() => useStarsBySpectralClassList('G'), { wrapper });
        await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
        expect(result.current.items).toEqual([]);
        service.fetchStarsBySpectralClass.mockResolvedValue([]);
        const callsBefore = service.fetchStarsBySpectralClass.mock.calls.length;
        result.current.retry();
        await waitFor(() => expect(service.fetchStarsBySpectralClass.mock.calls.length).toBe(callsBefore + 1));
    });
});
