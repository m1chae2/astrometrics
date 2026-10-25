/**
 * @fileoverview Tests of the Observation Planner: the layout, the Tonight
 * panel (chart text alternative, candidates, refresh, planning time), the night
 * plan (pick, create, edit, conflict, reload on `plan:changed`, states), the
 * Ekos summary, the `openInPlanner` listener, and the mode registry (the old
 * name opens the Planner; "Open in Planner" from Observation Execution lands
 * here).
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CandidateStatus, Confidence, RankedUse } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();
const timeSeriesProps = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/components/plotting/TimeSeriesPlot', () => ({
    TimeSeriesPlot: (props: unknown) => {
        timeSeriesProps(props);
        return <div>time series</div>;
    },
}));
vi.mock('../TargetListPanel', () => ({ TargetListPanel: () => <div>target list stub</div> }));

import { BackendError } from '../../common/services/backendError';
import { ToastProvider } from '../../common/components/ToastProvider';
import { TargetProvider } from '../../common/context/TargetContext';
import { PlanningTimeProvider, usePlanningTime } from '../../common/context/PlanningTimeContext';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from '../../common/constants/displayFlags';
import { normalizeAppMode } from '../../common/utils/normalizeAppMode';
import { handlePlanChanged } from '../../common/utils/planChanged';
import { navigateToElement } from '../../common/utils/displayCoordinator';
import { OBSERVATION_PLANNER_MODE } from '../../common/components/quality/useStatementActions';
import { TonightCandidatesPanel } from '../../observationExecution/TonightCandidatesPanel';
import { ObservationPlanner } from '../ObservationPlanner';
import { describeVisibility } from '../NightVisibilityChart';
import { exampleNightVisibility } from '../../common/examples';

/** A ranking with one imaging list of four candidates, one of them new. */
const ranking = {
    at: '2026-10-24T22:00:00-06:00',
    generatedAt: '2026-10-24T22:00:05-06:00',
    sky: { at: '2026-10-24T22:00:00-06:00' },
    notes: ['Ranking note.'],
    lists: [
        {
            use: RankedUse.IMAGING,
            gainDescription: 'Noise cut per extra hour',
            candidates: ['M 81', 'M 82', 'NGC 2403', 'M 51'].map((targetId, index) => ({
                use: RankedUse.IMAGING,
                targetId,
                rank: index + 1,
                status: index === 3 ? CandidateStatus.NEW : CandidateStatus.RANKED,
                gainPerHourFraction: 0.18,
                confidence: Confidence.DESIGN_ESTIMATE,
                reason: `Reason for ${targetId}`,
            })),
        },
    ],
};

/** A stored plan with two queue entries, one checklist item and a framing. */
const storedSession = (revision = 3) => ({
    id: 'plan-1',
    nightDate: '2026-10-24',
    revision,
    queue: [
        {
            id: 'entry-1',
            observationPackageId: 'pkg-1',
            targetId: 'M 81',
            status: 'PENDING',
            ditherConfig: { enabled: true, everyNFrames: 3, pixels: 5 },
            exposureRequests: [{ frameType: 'LIGHT', filter: 'L', exposureSec: 60, count: 40, gainSetting: 100, offset: 10, binning: 1 }],
        },
        {
            id: 'entry-2',
            observationPackageId: 'pkg-2',
            targetId: 'M 82',
            status: 'PENDING',
            exposureRequests: [{ frameType: 'LIGHT', filter: 'R', exposureSec: 120, count: 20 }],
        },
    ],
    checklist: [{ itemId: 'item-1', rule: 'focus_drift', note: 'Refocus at 22:00', tickedAt: null }],
    framing: { centerRaDeg: 148.888, centerDecDeg: 69.065, rotationDeg: 12, panels: [] },
});

/** The replies the fake backend gives, replaced per test. */
let replies: Record<string, unknown>;

/**
 * Sets the fake backend's replies.
 * @param overrides Replies by method name. An `Error` is thrown; a function is called.
 */
function setBackend(overrides: Record<string, unknown> = {}) {
    replies = {
        'advisory:target_report': { targetId: 'M 81', statements: [], needsAttention: { warningCount: 0 } },
        'advisory:tonight': ranking,
        'plan:get': (params: { session_id?: string }) =>
            params.session_id ? storedSession() : [{ id: 'plan-1', status: 'PLANNED', nightDate: '2026-10-24', entryCount: 2 }],
        'plan:edit': (params: { expected_revision?: number }) => ({
            sessionId: 'plan-1',
            previousRevision: params.expected_revision ?? 0,
            revision: (params.expected_revision ?? 0) + 1,
            changes: [],
            session: storedSession((params.expected_revision ?? 0) + 1),
        }),
        ...overrides,
    };
    callBackendMock.mockImplementation(async (method: string, params: unknown) => {
        const reply = replies[method];
        if (reply instanceof Error) throw reply;
        if (typeof reply === 'function') return (reply as (value: unknown) => unknown)(params);
        return reply ?? [];
    });
}

/** Shows the shared planning time and lets a test change it. */
const PlanningTimeProbe: React.FC = () => {
    const { setPlanningTime } = usePlanningTime();
    return (
        <button type="button" onClick={() => setPlanningTime('2026-10-25T22:00:00-06:00')}>
            probe set time
        </button>
    );
};

let client: QueryClient;

/**
 * Renders the Planner with a selected target.
 * @param children Extra elements rendered beside it (inside the providers).
 * @return The user-event handle.
 */
function renderPlanner(children: React.ReactNode = null) {
    window.localStorage.setItem('selectedTarget', 'M 81');
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
        <QueryClientProvider client={client}>
            <ToastProvider>
                <TargetProvider>
                    <PlanningTimeProvider>
                        <ObservationPlanner />
                        {children}
                    </PlanningTimeProvider>
                </TargetProvider>
            </ToastProvider>
        </QueryClientProvider>,
    );
    return user;
}

beforeEach(() => {
    callBackendMock.mockReset();
    timeSeriesProps.mockClear();
    window.localStorage.clear();
    setBackend();
});

describe('layout', () => {
    /** The header names the target and the planning time; Targets, Data Quality and the right column are present. */
    it('shows the header, the target list, the Data Quality panel and the right column', async () => {
        renderPlanner();
        const header = screen.getByRole('banner');
        expect(within(header).getByText('M 81')).toBeTruthy();
        expect(within(header).getByText('Planning time: Now')).toBeTruthy();
        expect(screen.getByText('target list stub')).toBeTruthy();
        expect(document.getElementById('data-quality-panel')).toBeTruthy();
        expect(await screen.findByText('Tonight')).toBeTruthy();
        expect(screen.getByText('Night plan')).toBeTruthy();
        expect(screen.getByRole('region', { name: 'Ekos summary' })).toBeTruthy();
    });

    /** Without a target the Data Quality panel and the chart ask for one. */
    it('asks for a target when none is chosen', async () => {
        window.localStorage.clear();
        client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        render(
            <QueryClientProvider client={client}>
                <ToastProvider>
                    <TargetProvider>
                        <PlanningTimeProvider>
                            <ObservationPlanner />
                        </PlanningTimeProvider>
                    </TargetProvider>
                </ToastProvider>
            </QueryClientProvider>,
        );
        expect((await screen.findAllByText('Choose a target')).length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText('No target chosen')).toBeTruthy();
    });
});

describe('Tonight', () => {
    /** The chart is marked as example data and states what it shows in words. */
    it('marks the chart as example data and gives a text alternative', async () => {
        renderPlanner();
        const chart = await screen.findByRole('region', { name: 'Altitude over the night' });
        expect(chart.getAttribute('data-example')).toBe('true');
        expect(within(chart).getByTestId('example-data-badge')).toBeTruthy();
        const description = describeVisibility('M 81', exampleNightVisibility);
        const picture = within(chart).getByRole('img');
        expect(picture.getAttribute('aria-label')).toBe(description);
        expect(description).toMatch(/Clear of the horizon limit from .* to /);
        expect(description).toMatch(/Highest at .*, 72 degrees/);
        expect(description).toMatch(/Usable from .* to /);
        expect(description).toMatch(/Altitude limit used: 30 degrees/);
        expect(description).toMatch(/Moon 35% lit/);
        expect(within(chart).getByText(/^Usable: /)).toBeTruthy();
    });

    /** The chart passes the spans as named bands and the limit as a labeled line. */
    it('names the shaded spans and the altitude limit', async () => {
        renderPlanner();
        await screen.findByRole('region', { name: 'Altitude over the night' });
        const props = timeSeriesProps.mock.calls[timeSeriesProps.mock.calls.length - 1]?.[0] as {
            bands: { label: string }[];
            referenceLines: { label: string }[];
            data: { name: string }[];
        };
        expect(props.bands.map((band) => band.label)).toEqual(['Between twilights', 'Clear of horizon limit', 'Usable']);
        expect(props.referenceLines[0].label).toBe('Altitude limit 30 deg');
        expect(props.data.map((series) => series.name)).toEqual(['M 81 altitude', 'Moon altitude']);
    });

    /** Candidates come in the returned order, three per list, with Show on sky and Switch to. */
    it('lists the candidates with Show on sky and Switch to', async () => {
        renderPlanner();
        const list = await screen.findByRole('region', { name: 'Imaging candidates' });
        expect(within(list).getAllByRole('listitem').length).toBeGreaterThanOrEqual(3);
        expect(within(list).getByRole('button', { name: 'Show on sky: M 82' })).toBeTruthy();
        expect(within(list).getByRole('button', { name: 'Switch to M 82' })).toBeTruthy();
        expect(within(list).queryByRole('button', { name: /Open in Planner/ })).toBeNull();
        await userEvent.setup().click(within(list).getByRole('button', { name: 'Show all 4' }));
        expect(within(list).getByText('New')).toBeTruthy();
    });

    /** "Switch to" changes the current target in the header. */
    it('switches the current target', async () => {
        const user = renderPlanner();
        const list = await screen.findByRole('region', { name: 'Imaging candidates' });
        await user.click(within(list).getByRole('button', { name: 'Switch to M 82' }));
        await waitFor(() => expect(within(screen.getByRole('banner')).getByText('M 82')).toBeTruthy());
    });

    /** "Refresh" reads the ranking again. */
    it('refreshes the ranking', async () => {
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Imaging candidates' });
        const before = callBackendMock.mock.calls.filter(([method]) => method === 'advisory:tonight').length;
        await user.click(screen.getByRole('button', { name: 'Refresh' }));
        await waitFor(() =>
            expect(callBackendMock.mock.calls.filter(([method]) => method === 'advisory:tonight').length).toBe(before + 1),
        );
    });

    /** Changing the planning time reads the ranking at that time. */
    it('reads the ranking at the planning time', async () => {
        const user = renderPlanner(<PlanningTimeProbe />);
        await screen.findByRole('region', { name: 'Imaging candidates' });
        await user.click(screen.getByRole('button', { name: 'probe set time' }));
        await waitFor(() =>
            expect(callBackendMock).toHaveBeenCalledWith('advisory:tonight', { at: '2026-10-25T22:00:00-06:00' }),
        );
        expect(await screen.findByText(/Planning time: fixed at/)).toBeTruthy();
    });

    /** A failed ranking shows the error state with "Try again". */
    it('shows an error when the ranking cannot load', async () => {
        setBackend({ 'advisory:tonight': new Error('boom') });
        renderPlanner();
        expect(await screen.findByText('Could not load the candidate ranking')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    });

    /** An empty ranking shows the empty state. */
    it('shows an empty state when the ranking has no list', async () => {
        setBackend({ 'advisory:tonight': { at: ranking.at, lists: [], sky: ranking.sky } });
        renderPlanner();
        expect(await screen.findByText('No candidates yet. Sync to fetch new frames.')).toBeTruthy();
    });
});

describe('night plan', () => {
    /** The first plan returned is shown, with its queue, dither, checklist and framing. */
    it('shows the stored plan', async () => {
        renderPlanner();
        const queue = await screen.findByRole('region', { name: 'Plan queue' });
        expect(within(queue).getByText('Dither every 3 frames, 5 px')).toBeTruthy();
        expect(within(queue).getByText('No dither')).toBeTruthy();
        expect(within(queue).getByText(/40 x 60 s, filter L, gain 100, offset 10, bin 1/)).toBeTruthy();
        const checklist = screen.getByRole('region', { name: 'Plan checklist' });
        expect(within(checklist).getByText('focus_drift')).toBeTruthy();
        const framing = screen.getByRole('region', { name: 'Plan framing' });
        expect(within(framing).getByText('148.8880 deg')).toBeTruthy();
        expect(within(framing).getByText(/12 deg \(degrees east of north/)).toBeTruthy();
        expect(screen.getByText('Revision 3')).toBeTruthy();
    });

    /** The user can pick another plan from the list; the plan is then read by id. */
    it('picks a plan from the list', async () => {
        setBackend({
            'plan:get': (params: { session_id?: string }) =>
                params.session_id
                    ? { ...storedSession(), id: params.session_id }
                    : [
                          { id: 'plan-1', status: 'PLANNED', nightDate: '2026-10-24', entryCount: 2 },
                          { id: 'plan-2', status: 'PLANNED', nightDate: '2026-10-23', entryCount: 1 },
                      ],
        });
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        await user.selectOptions(screen.getByRole('combobox', { name: /Plan/ }), 'plan-2');
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('plan:get', { session_id: 'plan-2' }));
    });

    /** With no plan, the empty state says what to do; "Create tonight's plan" makes one and shows it. */
    it('creates a plan when there is none', async () => {
        let created = false;
        setBackend({
            'plan:get': (params: { session_id?: string }) => {
                if (params.session_id) return { ...storedSession(), queue: [], checklist: [], framing: null };
                return created ? [{ id: 'plan-1', status: 'PLANNED', nightDate: '2026-10-24', entryCount: 0 }] : [];
            },
            'plan:create': () => {
                created = true;
                return { ...storedSession(), queue: [], checklist: [], framing: null };
            },
        });
        const user = renderPlanner();
        expect(await screen.findByText(/No night plan yet/)).toBeTruthy();
        await user.click(screen.getByRole('button', { name: "Create tonight's plan" }));
        expect(await screen.findByText('The queue is empty. Send a recommendation to the plan to add to it.')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('plan:create', {});
    });

    /** A failed plan list shows the error state. */
    it('shows an error when the plan list cannot load', async () => {
        setBackend({ 'plan:get': new Error('boom') });
        renderPlanner();
        expect(await screen.findByText('Could not load the night plan list')).toBeTruthy();
    });

    /** Moving an entry sends the whole new order with the revision last read. */
    it('moves an entry with the expected revision', async () => {
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        await user.click(screen.getByRole('button', { name: 'Move M 81 down' }));
        await waitFor(() =>
            expect(callBackendMock).toHaveBeenCalledWith('plan:edit', {
                session_id: 'plan-1',
                expected_revision: 3,
                order: ['entry-2', 'entry-1'],
            }),
        );
        expect(await screen.findByText('Revision 4')).toBeTruthy();
    });

    /** Editing exposures sends set_exposures; a bad number is refused on screen without a call. */
    it('edits exposures', async () => {
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        await user.click(screen.getByRole('button', { name: 'Edit exposures of M 81' }));
        const form = screen.getByRole('form', { name: 'Edit exposures of M 81' });
        const exposure = within(form).getByLabelText('Exposure (s)');
        await user.clear(exposure);
        await user.type(exposure, '0');
        await user.click(within(form).getByRole('button', { name: 'Save exposures' }));
        expect(within(form).getByRole('alert').textContent).toMatch(/exposure must be a number above 0/);
        expect(callBackendMock.mock.calls.some(([method]) => method === 'plan:edit')).toBe(false);
        await user.clear(exposure);
        await user.type(exposure, '90');
        await user.click(within(form).getByRole('button', { name: 'Save exposures' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('plan:edit', expect.objectContaining({
            set_exposures: { 'entry-1': [expect.objectContaining({ exposureSec: 90, count: 40, filter: 'L', frameType: 'LIGHT' })] },
        })));
    });

    /** Removing asks first, then sends remove. */
    it('removes an entry after a confirmation', async () => {
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        await user.click(screen.getByRole('button', { name: 'Remove M 82 from the plan' }));
        expect(callBackendMock.mock.calls.some(([method]) => method === 'plan:edit')).toBe(false);
        await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }));
        await waitFor(() =>
            expect(callBackendMock).toHaveBeenCalledWith('plan:edit', expect.objectContaining({ remove: ['entry-2'] })),
        );
    });

    /** Ticking a checklist item calls the tick method with the revision. */
    it('ticks a checklist item', async () => {
        setBackend({
            'plan:tick_checklist_item': (params: { expected_revision?: number }) => ({
                sessionId: 'plan-1',
                previousRevision: 3,
                revision: 4,
                changes: [],
                session: { ...storedSession(4), checklist: [{ itemId: 'item-1', rule: 'focus_drift', tickedAt: '2026-10-24T21:00:00-06:00' }] },
            }),
        });
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan checklist' });
        await user.click(screen.getByRole('button', { name: 'Tick: focus_drift' }));
        await waitFor(() =>
            expect(callBackendMock).toHaveBeenCalledWith('plan:tick_checklist_item', {
                session_id: 'plan-1',
                item_id: 'item-1',
                expected_revision: 3,
            }),
        );
        expect(await screen.findByRole('button', { name: 'Tick again: focus_drift' })).toBeTruthy();
    });

    /** An item that came back is shown with the library's reason, as given. */
    it('shows items that came back with their reason', async () => {
        setBackend({
            'advisory:target_report': {
                targetId: 'M 81',
                statements: [
                    { rule: 'focus_drift', kind: 'warning', message: 'm', confidence: Confidence.MEASURED,
                      checklistReturn: { itemId: 'item-1', tickedAt: '2026-10-12T21:00:00-06:00', reason: 'stars still 28% wider on Oct 15' } },
                ],
                needsAttention: { warningCount: 1 },
            },
        });
        renderPlanner();
        const checklist = await screen.findByRole('region', { name: 'Plan checklist' });
        expect(await within(checklist).findByText(/stars still 28% wider on Oct 15/)).toBeTruthy();
    });

    /** A revision conflict reloads the plan and says so; the notice stays until dismissed. */
    it('reloads and says so on a revision conflict', async () => {
        setBackend({ 'plan:edit': new BackendError({ code: 'conflict', message: 'revision mismatch' }) });
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        const readsBefore = callBackendMock.mock.calls.filter(([method, params]) => method === 'plan:get' && params?.session_id).length;
        await user.click(screen.getByRole('button', { name: 'Move M 81 down' }));
        const alert = await screen.findByText('The plan changed in another window; reloaded. Make your change again.');
        expect(alert.closest('[role="alert"]')).toBeTruthy();
        await waitFor(() =>
            expect(callBackendMock.mock.calls.filter(([method, params]) => method === 'plan:get' && params?.session_id).length).toBeGreaterThan(readsBefore),
        );
        await user.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(screen.queryByText(/reloaded/)).toBeNull();
    });

    /** A refusal shows the library's reason until dismissed. */
    it('shows the library reason when an edit is refused', async () => {
        setBackend({ 'plan:edit': new BackendError({ code: 'invalid_argument', message: 'An entry that is running cannot be removed.' }) });
        const user = renderPlanner();
        await screen.findByRole('region', { name: 'Plan queue' });
        await user.click(screen.getByRole('button', { name: 'Move M 81 down' }));
        expect((await screen.findByText('An entry that is running cannot be removed.')).closest('[role="alert"]')).toBeTruthy();
    });

    /** `plan:changed` marks the plan reads stale, so the plan is read again and shows the new revision. */
    it('reloads the plan when plan:changed arrives', async () => {
        renderPlanner();
        expect(await screen.findByText('Revision 3')).toBeTruthy();
        replies['plan:get'] = (params: { session_id?: string }) =>
            params.session_id ? storedSession(5) : [{ id: 'plan-1', status: 'PLANNED', nightDate: '2026-10-24', entryCount: 2 }];
        let fromAnotherWindow = false;
        await act(async () => {
            fromAnotherWindow = handlePlanChanged(client, { sessionId: 'plan-1', revision: 5 });
        });
        expect(fromAnotherWindow).toBe(true);
        expect(await screen.findByText('Revision 5')).toBeTruthy();
    });
});

describe('Ekos summary', () => {
    /** The panel is example data, the rows are in order, and "done in Ekos" is off with a visible reason. */
    it('shows example rows with copy buttons and a disabled done tick', async () => {
        renderPlanner();
        const summary = await screen.findByRole('region', { name: 'Ekos summary' });
        expect(summary.getAttribute('data-example')).toBe('true');
        expect(within(summary).getByTestId('example-data-badge')).toBeTruthy();
        expect(within(summary).getAllByRole('listitem').length).toBe(2);
        const done = within(summary).getAllByRole('checkbox')[0] as HTMLInputElement;
        expect(done.disabled).toBe(true);
        const reason = document.getElementById(done.getAttribute('aria-describedby') as string);
        expect(reason?.textContent).toMatch(/arrives with the library plan update/);
    });

    /** "Copy" puts the row's text on the clipboard and announces "Copied" politely; the button names its row. */
    it('copies a row and announces it', async () => {
        const user = renderPlanner();
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
        const button = await screen.findByRole('button', { name: /^Copy row 1: L, 60 s/ });
        await user.click(button);
        expect(writeText).toHaveBeenCalledWith('L, 60 s, 40 frames, gain 100, offset 10, bin 1');
        const live = (await screen.findByText('Copied row 1')).closest('[aria-live]');
        expect(live?.getAttribute('aria-live')).toBe('polite');
    });
});

describe('Open in Planner and the mode registry', () => {
    /** `openInPlanner` makes the target current and moves focus to the Data Quality panel. */
    it('sets the target and focuses the Data Quality panel', async () => {
        renderPlanner();
        await screen.findByText('Tonight');
        act(() => {
            window.dispatchEvent(new CustomEvent('astrometrics:openInPlanner', { detail: { targetId: 'M 13' } }));
        });
        await waitFor(() => expect(within(screen.getByRole('banner')).getByText('M 13')).toBeTruthy());
        await waitFor(() => expect(document.activeElement?.id).toBe('data-quality-panel'));
    });

    /** Observation Manager is the registered display again; the branch's two names open it. */
    it('registers Observation Manager and maps the Planner and Execution names to it', () => {
        expect(DISPLAY_DEFINITIONS).toContainEqual({ mode: 'Observation Manager', flag: 'enable_observation' });
        expect(DISPLAY_DEFINITIONS.some((definition) => definition.mode === 'Observation Planner')).toBe(false);
        expect(DISPLAY_DEFINITIONS.some((definition) => definition.mode === 'Observation Execution')).toBe(false);
        expect(normalizeAppMode('Observation Planner')).toBe('Observation Manager');
        expect(normalizeAppMode('Observation Execution')).toBe('Observation Manager');
        expect(OBSERVATION_PLANNER_MODE).toBe('Observation Manager');
    });
});
