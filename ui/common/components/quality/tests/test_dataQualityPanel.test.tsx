/**
 * @fileoverview Tests of the Data Quality panel, the strip and the chip:
 * every state, the example badge, and the actions.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StatementKind } from '../../../types/backendTypes';

const callBackendMock = vi.fn();
const navigateMock = vi.fn().mockResolvedValue({ handledRemotely: false });

vi.mock('../../../services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../../utils/displayCoordinator', () => ({
    navigateToElement: (...args: unknown[]) => navigateMock(...args),
}));

import { DataQualityPanel } from '../DataQualityPanel';
import { QualityStrip } from '../QualityStrip';
import { QualityChip } from '../QualityChip';
import { calibrationStatement, makeReport, makeStatement } from './qualityTestData';

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
    navigateMock.mockClear();
});

describe('DataQualityPanel states', () => {
    /** No target: asks for one and does not fetch. */
    it('says "Choose a target" and does not fetch', () => {
        renderWithQuery(<DataQualityPanel targetId={null} />);
        expect(screen.getByText('Choose a target')).toBeTruthy();
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** Loading is busy. */
    it('shows loading with aria-busy', () => {
        callBackendMock.mockReturnValue(new Promise(() => undefined));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        expect(screen.getByText('Loading…').getAttribute('aria-busy')).toBe('true');
    });

    /** A real empty reply shows the empty state, never example data. */
    it('shows the empty state for an empty reply', async () => {
        callBackendMock.mockResolvedValue({ targetId: 'M 81', needsAttention: { warningCount: 0 } });
        const { container } = renderWithQuery(<DataQualityPanel targetId="M 81" />);
        expect(await screen.findByText('No frames yet. Sync to fetch new frames.')).toBeTruthy();
        expect(container.querySelector('[data-example="true"]')).toBeNull();
    });

    /** A failed read shows the error state with "Try again", which reads again. */
    it('shows the error state and retries', async () => {
        const user = userEvent.setup();
        callBackendMock.mockRejectedValueOnce(new Error('boom'));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        expect(await screen.findByText('Could not load the quality report')).toBeTruthy();
        callBackendMock.mockResolvedValue(makeReport([makeStatement()]));
        await user.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('Star width is high.')).toBeTruthy();
    });

    /** A not-enough-data statement is a normal, greyed state with the library's reason. */
    it('shows a not-enough-data statement greyed with its word', async () => {
        callBackendMock.mockResolvedValue(
            makeReport([makeStatement({ kind: StatementKind.INSUFFICIENT_DATA, severity: null, message: 'Only 2 frames.' })]),
        );
        const { container } = renderWithQuery(<DataQualityPanel targetId="M 81" />);
        expect(await screen.findByText('Only 2 frames.')).toBeTruthy();
        expect(container.querySelector('.statement-item--insufficient_data')).not.toBeNull();
        expect(screen.getAllByText('Not enough data').length).toBeGreaterThan(0);
    });
});

describe('DataQualityPanel content', () => {
    /** Shows each effective-time number with its own label, and the loss parts apart. */
    it('labels both effective times and keeps loss parts apart', async () => {
        callBackendMock.mockResolvedValue(makeReport([makeStatement()]));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        expect(await screen.findByText('Weight-effective time')).toBeTruthy();
        expect(screen.getByText('Efficiency-effective time')).toBeTruthy();
        expect(screen.getByText(/Set-aside time: 3 min/)).toBeTruthy();
        expect(screen.getByText('Set aside, by cause')).toBeTruthy();
        expect(screen.getByText('Kept-frame deficit, by cause')).toBeTruthy();
        expect(screen.getByText('A note from the library.')).toBeTruthy();
        expect(screen.getByText('Sensor temperature is not stored.')).toBeTruthy();
        expect(screen.getByText(/Outcome: as forecast/)).toBeTruthy();
        expect(screen.getByText(/Paired/)).toBeTruthy();
        expect(document.querySelector('[data-example="true"]')).toBeNull();
    });

    /** Shows the starting point for a new target with the Starting point mark. */
    it('shows the starting point for a new target', async () => {
        callBackendMock.mockResolvedValue({
            targetId: 'New',
            needsAttention: { warningCount: 0 },
            startingPoint: {
                exposureSeconds: 30,
                exposureReason: 'sky limited',
                clippingCapSeconds: 90,
                clippingCapReason: 'bright star',
                confidence: 'design_estimate',
            },
        });
        renderWithQuery(<DataQualityPanel targetId="New" />);
        expect(await screen.findByText(/Starting point/)).toBeTruthy();
        expect(screen.getByText(/sky limited/)).toBeTruthy();
        expect(screen.getByText('Tentative')).toBeTruthy();
    });

    /** Evidence fires showFrames with the row ids and statement text; star results stay as text. */
    it('fires showFrames from Evidence', async () => {
        const user = userEvent.setup();
        callBackendMock.mockResolvedValue(
            makeReport([makeStatement({ evidence: [{ rowId: 7 }, { rowId: 9 }, { starResult: { starId: 'S', runId: 'R', metric: 'm' } }] })]),
        );
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        await user.click(await screen.findByRole('button', { name: 'Evidence' }));
        expect(navigateMock).toHaveBeenCalledWith(
            expect.objectContaining({
                action: 'showFrames',
                payload: { rowIds: [7, 9], targetId: 'M 81', statementText: 'Star width is high.' },
            }),
        );
    });

    /** A calibration statement offers "Show in calibration library". */
    it('fires showCalibrationRow', async () => {
        const user = userEvent.setup();
        callBackendMock.mockResolvedValue(makeReport([calibrationStatement]));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        await user.click(await screen.findByRole('button', { name: 'Show in calibration library' }));
        expect(navigateMock).toHaveBeenCalledWith(expect.objectContaining({ action: 'showCalibrationRow' }));
    });

    /** Send to plan is retired: no button and no reason text. */
    it('offers no Send to plan button', async () => {
        callBackendMock.mockResolvedValue(makeReport([calibrationStatement]));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        await screen.findByRole('button', { name: 'Show in calibration library' });
        expect(screen.queryByRole('button', { name: 'Send to plan' })).toBeNull();
        expect(screen.queryByText(/Send to plan/)).toBeNull();
    });
});

describe('QualityStrip', () => {
    /** Takes the first warning and first recommendation in the returned order, labels example loss. */
    it('shows the first warning and recommendation and example loss with the badge', async () => {
        callBackendMock.mockResolvedValue(
            makeReport([
                makeStatement({ rule: 'a', kind: StatementKind.RECOMMENDATION, message: 'Rec first.' }),
                makeStatement({ rule: 'b', message: 'Warn one.' }),
                makeStatement({ rule: 'c', message: 'Warn two.' }),
                makeStatement({ rule: 'd', kind: StatementKind.RECOMMENDATION, message: 'Rec two.' }),
            ]),
        );
        const { container } = renderWithQuery(<QualityStrip targetId="M 81" />);
        expect(await screen.findByText('Warn one.')).toBeTruthy();
        expect(screen.getByText('Rec first.')).toBeTruthy();
        expect(screen.queryByText('Warn two.')).toBeNull();
        expect(screen.getByText("Tonight's loss")).toBeTruthy();
        expect(screen.getByText('Example data')).toBeTruthy();
        expect(container.querySelector('[data-example="true"]')).not.toBeNull();
    });

    /** Uses the night's target row when a night is given. */
    it('reads the night row when a night is given', async () => {
        callBackendMock.mockResolvedValue({
            nightId: 'N',
            targets: [{ targetId: 'M 81', statements: [makeStatement({ message: 'Night warning.' })] }],
        });
        renderWithQuery(<QualityStrip targetId="M 81" nightId="N" />);
        expect(await screen.findByText('Night warning.')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('history:night_quality', { session_id: 'N' });
    });

    /** "Open in Planner" fires openInPlanner. */
    it('fires openInPlanner', async () => {
        const user = userEvent.setup();
        callBackendMock.mockResolvedValue(makeReport([makeStatement()]));
        renderWithQuery(<QualityStrip targetId="M 81" />);
        await user.click(await screen.findByRole('button', { name: 'Open in Planner' }));
        expect(navigateMock).toHaveBeenCalledWith(
            expect.objectContaining({ action: 'openInPlanner', payload: { targetId: 'M 81' } }),
        );
    });

    /** Error state. */
    it('shows the error state', async () => {
        callBackendMock.mockRejectedValue(new Error('x'));
        renderWithQuery(<QualityStrip targetId="M 81" />);
        expect(await screen.findByText('Could not load the quality report')).toBeTruthy();
    });
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
