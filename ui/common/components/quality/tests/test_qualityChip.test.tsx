/**
 * @fileoverview Tests of the quality chip: the first statement, "Show all N",
 * and the empty and no-target states.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

const callBackendMock = vi.fn();

vi.mock('../../../services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { QualityChip } from '../QualityChip';
import { makeStatement } from './qualityTestData';

/**
 * Renders a component inside a fresh query client.
 * @param ui The component.
 * @return The render result.
 */
function renderWithQuery(ui: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
    callBackendMock.mockReset();
});

describe('QualityChip', () => {
    /** Shows the first statement and "Show all N". */
    it('shows the first statement with Show all N', async () => {
        callBackendMock.mockResolvedValue([
            makeStatement({ rule: 'a', message: 'First.' }),
            makeStatement({ rule: 'b', message: 'Second.' }),
        ]);
        renderWithQuery(<QualityChip targetId="M 81" objectId="Vega" />);
        expect(await screen.findByText('First.')).toBeTruthy();
        expect(screen.queryByText('Second.')).toBeNull();
        expect(screen.getByRole('button', { name: 'Show all 2' })).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('advisory:result', { target: 'M 81', object_id: 'Vega' });
    });

    /** Empty and no-target states. */
    it('shows empty and no-target states', async () => {
        callBackendMock.mockResolvedValue([]);
        const { unmount } = renderWithQuery(<QualityChip targetId="M 81" objectId="Vega" />);
        expect(await screen.findByText('No quality statements about this result.')).toBeTruthy();
        unmount();
        renderWithQuery(<QualityChip targetId={null} objectId={null} />);
        expect(screen.getByText('Choose a target')).toBeTruthy();
    });
});
