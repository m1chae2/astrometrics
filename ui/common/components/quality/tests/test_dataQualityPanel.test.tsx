/**
 * @fileoverview Tests of the Data Quality panel, the strip and the chip:
 * every state, the example badge, the actions and the Send to plan outcomes.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BackendError } from '../../../services/backendError';
import { StatementKind } from '../../../types/backendTypes';

const callBackendMock = vi.fn();
const navigateMock = vi.fn().mockResolvedValue({ handledRemotely: false });

vi.mock('../../../services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../../utils/displayCoordinator', () => ({
    navigateToElement: (...args: unknown[]) => navigateMock(...args),
}));

import { planQueryKeys } from '../../../queries/qualityQueryKeys';
import { DataQualityPanel } from '../DataQualityPanel';
import { QualityStrip } from '../QualityStrip';
import { QualityChip } from '../QualityChip';
import { StatementItem } from '../StatementItem';
import { useStatementActions } from '../useStatementActions';
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

    /** While the library cannot take statements, Send to plan is disabled with visible linked text. */
    it('disables Send to plan with a visible reason', async () => {
        callBackendMock.mockResolvedValue(makeReport([calibrationStatement]));
        renderWithQuery(<DataQualityPanel targetId="M 81" />);
        const button = await screen.findByRole('button', { name: 'Send to plan' });
        expect((button as HTMLButtonElement).disabled).toBe(true);
        const reasonId = button.getAttribute('aria-describedby') as string;
        expect(document.getElementById(reasonId)?.textContent).toBe('Send to plan arrives with the library plan update');
    });
});

/**
 * A test host that renders one statement with live Send to plan.
 * @return The host.
 */
const SendHost: React.FC = () => {
    const actions = useStatementActions('M 81', true);
    return (
        <ul>
            <StatementItem itemKey="k" statement={calibrationStatement} actions={actions} />
        </ul>
    );
};

describe('Send to plan outcomes', () => {
    /** Mocks plan:get (list and one session) and plan:edit. */
    const mockPlan = (editResult: () => Promise<unknown>, plans: unknown[] = [{ id: 'S1' }]) => {
        callBackendMock.mockImplementation((method: string, params: Record<string, unknown>) => {
            if (method === 'plan:get') return Promise.resolve(params.session_id ? { id: 'S1', revision: 4 } : plans);
            if (method === 'plan:edit') return editResult();
            if (method === 'plan:create') return Promise.resolve({ id: 'S2', revision: 0 });
            return Promise.reject(new Error(`unexpected ${method}`));
        });
    };

    /** An accepted statement names the entry. */
    it('adds and names the entry', async () => {
        const user = userEvent.setup();
        mockPlan(() => Promise.resolve({ changes: [{ kind: 'checklist_added', detail: 'Dark frames' }] }));
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        client.setQueryData(planQueryKeys.list(), []);
        client.setQueryData(planQueryKeys.session('S1'), { id: 'S1' });
        render(
            <QueryClientProvider client={client}>
                <SendHost />
            </QueryClientProvider>,
        );
        await user.click(screen.getByRole('button', { name: 'Send to plan' }));
        expect(await screen.findByText(/Added to the plan: Dark frames/)).toBeTruthy();
        expect(client.getQueryState(planQueryKeys.list())?.isInvalidated).toBe(true);
        expect(client.getQueryState(planQueryKeys.session('S1'))?.isInvalidated).toBe(true);
        const editCall = callBackendMock.mock.calls.find((call) => call[0] === 'plan:edit');
        expect(editCall?.[1]).toMatchObject({ session_id: 'S1', expected_revision: 4 });
    });

    /** A repeat is reported as merged. */
    it('reports a merge', async () => {
        const user = userEvent.setup();
        mockPlan(() => Promise.resolve({ changes: [{ kind: 'queue_merged', detail: 'L 60 s' }] }));
        renderWithQuery(<SendHost />);
        await user.click(screen.getByRole('button', { name: 'Send to plan' }));
        expect(await screen.findByText(/Merged into the plan/)).toBeTruthy();
    });

    /** A rejection shows the library's reason as an alert that stays until dismissed. */
    it('shows a rejection as an alert until dismissed', async () => {
        const user = userEvent.setup();
        mockPlan(() => Promise.reject(new BackendError({ code: 'invalid_argument', message: 'Gain 900 is not allowed.' })));
        renderWithQuery(<SendHost />);
        await user.click(screen.getByRole('button', { name: 'Send to plan' }));
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('Gain 900 is not allowed.');
        await user.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(screen.queryByRole('alert')).toBeNull();
    });

    /** A revision conflict says so and keeps the statement. */
    it('reports a revision conflict and keeps the statement', async () => {
        const user = userEvent.setup();
        mockPlan(() => Promise.reject(new BackendError({ code: 'conflict', message: 'stale' })));
        renderWithQuery(<SendHost />);
        await user.click(screen.getByRole('button', { name: 'Send to plan' }));
        expect(await screen.findByText(/The plan changed in another window; reloaded\. Send again\?/)).toBeTruthy();
        expect(screen.getByText('Take new darks.')).toBeTruthy();
    });

    /** With no plan, "Create tonight's plan" makes one and sends. */
    it('offers to create a plan when there is none', async () => {
        const user = userEvent.setup();
        mockPlan(() => Promise.resolve({ changes: [{ kind: 'checklist_added', detail: 'Dark frames' }] }), []);
        renderWithQuery(<SendHost />);
        await user.click(screen.getByRole('button', { name: 'Send to plan' }));
        await user.click(await screen.findByRole('button', { name: "Create tonight's plan" }));
        await waitFor(() => expect(screen.getByText(/Added to the plan/)).toBeTruthy());
        expect(callBackendMock.mock.calls.some((call) => call[0] === 'plan:create')).toBe(true);
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
