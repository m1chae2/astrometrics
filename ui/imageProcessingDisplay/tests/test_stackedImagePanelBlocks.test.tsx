/**
 * @fileoverview Tests for the Data tab's stack blocks: each stack shows its own total exposure
 * (and plate rows only from its own plate solution), in the order Final Stack, Spectral Stack,
 * Integration Goal.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StackedImagePanel } from '../components/StackedImagePanel';

const headerMock = vi.fn();
vi.mock('../../common/services/targetService', () => ({
    fetchTargetFrameHeader: (...args: unknown[]) => headerMock(...args),
}));
vi.mock('../components/DepthGoalRow', () => ({ DepthGoalRow: () => <div data-testid="depth-goal" /> }));
vi.mock('../components/StackSummaryPanel', () => ({
    StackSummaryPanel: ({ kind }: { kind: string }) => <div data-testid={`summary-${kind}`} />,
}));
vi.mock('../../common/fitsViewer/plateGeometry', () => ({
    headerEntriesToCards: (entries: Array<{ key: string }>) => ({ solved: entries.some(e => e.key === 'CD1_1') }),
    measurePlateGeometry: (cards: { solved: boolean }) => (cards.solved ? { id: 'geometry' } : null),
    formatPlateScale: () => '1.50 "/px',
    formatRotation: () => '12.0 deg',
}));

const FINAL = '/stacks/M31/M31_final.fits';
const SPECTRAL = '/stacks/M31/M31_spectral.fits';

/** Header entries for a stack, with or without a plate solution. */
const entries = (exptime: number, solved: boolean) => [
    { key: 'EXPTIME', value: exptime },
    ...(solved ? [{ key: 'CD1_1', value: 1 }] : []),
];

/** Answers each stack's header request from its own path. */
const answerHeaders = (byPath: Record<string, unknown>) => {
    headerMock.mockImplementation((_target: string, path: string) => Promise.resolve(byPath[path] ?? null));
};

/** Renders the panel for target M 31. */
const renderPanel = (path: string, spectralPath: string) =>
    render(<StackedImagePanel path={path} spectralPath={spectralPath}
        targetId="M 31" onView={() => {}} />);

describe('StackedImagePanel blocks', () => {
    beforeEach(() => { headerMock.mockReset(); });

    it('shows the luminance block only when there is no spectral stack', async () => {
        answerHeaders({ [FINAL]: entries(3600, true) });
        renderPanel(FINAL, '');
        expect(await screen.findByTestId('final-exposure')).toHaveTextContent('1.0h');
        expect(await screen.findByTestId('final-plate-scale')).toBeInTheDocument();
        expect(screen.queryByTestId('spectral-exposure')).toBeNull();
        expect(screen.queryByText('Spectral Stack:')).toBeNull();
        expect(screen.getByTestId('summary-imaging')).toBeInTheDocument();
        expect(screen.queryByTestId('summary-spectral')).toBeNull();
    });

    it('shows no luminance values for a spectral-only target', async () => {
        answerHeaders({ [SPECTRAL]: entries(1200, false) });
        renderPanel('', SPECTRAL);
        expect(await screen.findByTestId('spectral-exposure')).toHaveTextContent('20m');
        expect(screen.queryByTestId('final-exposure')).toBeNull();
        expect(screen.queryByTestId('final-plate-scale')).toBeNull();
        expect(screen.queryByTestId('summary-imaging')).toBeNull();
        expect(screen.getByTestId('summary-spectral')).toBeInTheDocument();
    });

    it('keeps each value under its own stack and ends with the depth goal', async () => {
        answerHeaders({ [FINAL]: entries(7200, true), [SPECTRAL]: entries(600, false) });
        const { container } = renderPanel(FINAL, SPECTRAL);
        await waitFor(() => expect(screen.getByTestId('spectral-exposure')).toHaveTextContent('10m'));
        expect(screen.getByTestId('final-exposure')).toHaveTextContent('2.0h');
        expect(screen.getByTestId('final-plate-scale')).toBeInTheDocument();
        expect(screen.getByTestId('final-plate-rotation')).toBeInTheDocument();
        expect(screen.queryByTestId('spectral-plate-scale')).toBeNull();
        expect(screen.queryByTestId('spectral-plate-rotation')).toBeNull();
        const order = Array.from(container.querySelectorAll('[data-testid], .stacked-image-panel__row'))
            .map(node => node.getAttribute('data-testid') ?? within(node as HTMLElement).getByText(/Stack:/).textContent);
        expect(order.indexOf('final-exposure')).toBeLessThan(order.indexOf('summary-imaging'));
        expect(order.indexOf('summary-imaging')).toBeLessThan(order.indexOf('Spectral Stack:'));
        expect(order.indexOf('spectral-exposure')).toBeLessThan(order.indexOf('summary-spectral'));
        expect(order.indexOf('summary-spectral')).toBeLessThan(order.indexOf('depth-goal'));
    });

    it('shows plate rows for a solved spectral stack and omits lines for a missing header', async () => {
        answerHeaders({ [SPECTRAL]: entries(300, true) });
        renderPanel(FINAL, SPECTRAL);
        expect(await screen.findByTestId('spectral-plate-scale')).toBeInTheDocument();
        expect(screen.queryByTestId('final-exposure')).toBeNull();
        expect(screen.queryByTestId('final-plate-scale')).toBeNull();
    });
});
