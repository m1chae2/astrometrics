/**
 * @fileoverview Tests of Observation Execution: the header, the order of the
 * panels, the example badges, the candidates and their states, an earlier
 * night, the tabs, and the disabled control panels.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CandidateStatus, Confidence, RankedUse, Severity, StatementKind } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();
const navigateMock = vi.fn().mockResolvedValue({ handledRemotely: false });

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/utils/displayCoordinator', () => ({
    navigateToElement: (...args: unknown[]) => navigateMock(...args),
}));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
    useTelescopeStatus: () => ({
        trackingStatus: 'Tracking',
        telemetry: { focuserPosition: 100, filter: 'L', guidingHistory: [], alignmentAttempts: [], alignmentActive: false },
    }),
}));
vi.mock('../../common/components/plotting/TimeSeriesPlot', () => ({ TimeSeriesPlot: () => <div>time series</div> }));
vi.mock('../../common/components/plotting/ScatterPlot', () => ({ ScatterPlot: () => <div>scatter</div> }));

import { ToastProvider } from '../../common/components/ToastProvider';
import { TargetProvider } from '../../common/context/TargetContext';
import { ObservationExecution } from '../ObservationExecution';

/** A ranking with one imaging list of four candidates, one of them new and thin. */
const ranking = {
    at: '2026-10-24T22:00:00-06:00',
    generatedAt: '2026-10-24T22:00:05-06:00',
    sky: { at: '2026-10-24T22:00:00-06:00' },
    notes: ['Ranking note.'],
    lists: [
        {
            use: RankedUse.IMAGING,
            gainDescription: 'Noise cut by one hour.',
            candidates: ['M 81', 'M 82', 'M 13', 'M 57'].map((targetId, index) => ({
                targetId,
                use: RankedUse.IMAGING,
                status: index === 3 ? CandidateStatus.NEW : CandidateStatus.RANKED,
                rank: index === 3 ? null : index + 1,
                gainPerHourFraction: 0.18,
                thinDataset: index === 3,
                reason: index === 3 ? 'No frames yet.' : '',
                confidence: Confidence.DESIGN_ESTIMATE,
            })),
        },
    ],
};

/** A night review with one target needing attention. */
const nightQuality = {
    nightId: '2026-10-23',
    needsAttention: { warningCount: 1, worstSeverity: Severity.WARNING },
    guiding: { available: true, flagged: false, rmsPerAxisArcsec: 0.8, lostFraction: 0.01, excursionCount: 2 },
    nightStatements: [
        { rule: 'focus_drift', kind: StatementKind.WARNING, severity: Severity.WARNING, message: 'Focus drifted.', confidence: Confidence.MEASURED },
    ],
    targets: [
        {
            targetId: 'M 81',
            goal: 'faint_point_source',
            framesTaken: 40,
            framesSetAside: 3,
            capturedSeconds: 2400,
            needsAttention: { warningCount: 1, worstSeverity: Severity.WARNING },
            lossAccount: {
                goal: 'faint_point_source',
                referenceName: 'Best night',
                setAsideSeconds: 180,
                causeUnknownSeconds: 60,
                causeUnknownReason: 'no log',
                transparencyIncluded: false,
                transparencyReason: '',
            },
        },
    ],
};

/**
 * Sets what each backend method returns.
 * @param overrides Replies by method name, or a function that throws or hangs.
 */
function setBackend(overrides: Record<string, unknown> = {}) {
    const replies: Record<string, unknown> = {
        'history:nights': [
            { nightId: '2026-10-23', nightDate: '2026-10-23', targetsImaged: ['M 81', 'M 82'], frameCount: 40 },
            { nightId: '2026-10-22', nightDate: '2026-10-22', targetsImaged: ['M 13'], frameCount: 10 },
        ],
        'advisory:tonight': ranking,
        'observatory:safety_status': { sections: [] },
        'advisory:target_report': { targetId: 'M 81', statements: [], needsAttention: { warningCount: 0 } },
        'history:night_quality': nightQuality,
        'history:target_changes': [],
        'history:live_session_status': { flags: ['Guiding was lost for 40 s.'], recent_guiding: { window_seconds: 600, sample_count: 0 }, analyze_file: 'a', as_of: 0 },
        ...overrides,
    };
    callBackendMock.mockImplementation(async (method: string) => {
        const reply = replies[method];
        if (reply instanceof Error) throw reply;
        if (typeof reply === 'function') return (reply as () => unknown)();
        return reply ?? [];
    });
}

/**
 * Renders the display with a selected target.
 * @return The user-event handle.
 */
function renderDisplay() {
    window.localStorage.setItem('selectedTarget', 'M 81');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
        <QueryClientProvider client={client}>
            <ToastProvider>
                <TargetProvider>
                    <ObservationExecution />
                </TargetProvider>
            </ToastProvider>
        </QueryClientProvider>,
    );
    return user;
}

beforeEach(() => {
    callBackendMock.mockReset();
    navigateMock.mockClear();
    window.localStorage.clear();
    setBackend();
});

describe('header', () => {
    /** The night selector lists the nights in the order returned, labeled by date, weekday and count. */
    it('lists nights in the order returned with a label', async () => {
        renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        const options = screen.getAllByRole('option');
        expect(options.map((option) => option.textContent)).toEqual([
            'Live session',
            '2026-10-23 (Fri), 2 targets',
            '2026-10-22 (Thu), 1 target',
        ]);
    });

    /** The header names the target, says it works at Now, and shows the sync lines with Never synced absent. */
    it('shows the current target, Working at Now and the sync lines', async () => {
        renderDisplay();
        expect(await screen.findByText('Working at Now')).toBeTruthy();
        expect(screen.getAllByText('M 81').length).toBeGreaterThan(0);
        expect(screen.getByText(/Logs: last sync/)).toBeTruthy();
        expect(screen.getByText(/Frames: last sync/)).toBeTruthy();
    });

    /** "Sync now" is off, with a visible reason linked by aria-describedby. */
    it('disables Sync now with a visible reason', async () => {
        renderDisplay();
        const button = await screen.findByRole('button', { name: 'Sync now' });
        expect((button as HTMLButtonElement).disabled).toBe(true);
        expect(button.getAttribute('aria-disabled')).toBe('true');
        const reason = document.getElementById(button.getAttribute('aria-describedby') as string);
        expect(reason?.textContent).toBe('Sync arrives with the library sync run');
    });

    /** A failed night list shows an alert. */
    it('shows an alert when the nights cannot load', async () => {
        setBackend({ 'history:nights': new Error('boom') });
        renderDisplay();
        expect(await screen.findByText('Could not load the night list')).toBeTruthy();
    });
});

describe('live session body', () => {
    /** The panels appear in the planned order: strip, verdict, candidates, frames. */
    it('orders the strip, verdict, candidates and frames', async () => {
        renderDisplay();
        await screen.findByRole('region', { name: 'Candidates now' });
        const names = ['Quality strip', 'Live session verdict', 'Candidates now', 'Frames of the night'];
        const positions = names.map((name) => screen.getByRole('region', { name }));
        for (let index = 1; index < positions.length; index += 1) {
            expect(positions[index - 1].compareDocumentPosition(positions[index]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        }
    });

    /** The verdict, the loss summary, the sync times and the frames each carry the example badge. */
    it('marks example panels with the badge and data-example', async () => {
        renderDisplay();
        await screen.findByRole('region', { name: 'Candidates now' });
        expect(screen.getByRole('region', { name: 'Live session verdict' }).getAttribute('data-example')).toBe('true');
        expect(screen.getByRole('region', { name: 'Frames of the night' }).getAttribute('data-example')).toBe('true');
        expect(screen.getAllByTestId('example-data-badge').length).toBeGreaterThanOrEqual(3);
        // The real candidates are not marked as example data.
        expect(screen.getByRole('region', { name: 'Candidates now' }).getAttribute('data-example')).toBeNull();
    });

    /** The verdict has the Tentative chip, a live region, and Details shows the raw flags. */
    it('shows the Tentative chip and the raw flags behind Details', async () => {
        const user = renderDisplay();
        const verdict = await screen.findByRole('region', { name: 'Live session verdict' });
        expect(within(verdict).getByText('Tentative')).toBeTruthy();
        expect(verdict.querySelectorAll('[aria-live="polite"]').length).toBe(2);
        await user.click(within(verdict).getByRole('button', { name: 'Details' }));
        expect(await within(verdict).findByText('Guiding was lost for 40 s.')).toBeTruthy();
    });

    /** The flags have an error state with Try again. */
    it('shows an error state for the flags', async () => {
        setBackend({ 'history:live_session_status': new Error('down') });
        const user = renderDisplay();
        const verdict = await screen.findByRole('region', { name: 'Live session verdict' });
        await user.click(within(verdict).getByRole('button', { name: 'Details' }));
        expect(await within(verdict).findByText('Could not load the live session status')).toBeTruthy();
        expect(within(verdict).getByRole('button', { name: 'Try again' })).toBeTruthy();
    });

    /** The frame list is a scroll region of example rows with a word for each status. */
    it('lists the example frames with status words', async () => {
        renderDisplay();
        const frames = await screen.findByRole('region', { name: 'Frames of the night' });
        expect(within(frames).getByText('Flagged')).toBeTruthy();
        expect(within(frames).getByText('In stack')).toBeTruthy();
        expect(within(frames).getByText('Not yet judged')).toBeTruthy();
        expect(within(frames).getAllByText('not measured').length).toBe(2);
    });
});

describe('candidates', () => {
    /** Each list shows its gain description and the top three, then Show all 4 keeps focus on the button. */
    it('shows the top three and Show all with focus kept', async () => {
        const user = renderDisplay();
        const candidates = await screen.findByRole('region', { name: 'Candidates now' });
        const list = await within(candidates).findByRole('region', { name: 'Imaging candidates' });
        expect(within(list).getByText('Noise cut by one hour.')).toBeTruthy();
        expect(within(list).queryByText('M 57')).toBeNull();
        const button = within(list).getByRole('button', { name: 'Show all 4' });
        await user.click(button);
        expect(within(list).getByText('M 57')).toBeTruthy();
        expect(document.activeElement).toBe(button);
        expect(button.textContent).toBe('Show fewer');
    });

    /** A new candidate shows an icon and the word, the thin-dataset note and the Tentative chip. */
    it('marks a new candidate and a thin dataset with words', async () => {
        const user = renderDisplay();
        const list = await screen.findByRole('region', { name: 'Imaging candidates' });
        await user.click(within(list).getByRole('button', { name: 'Show all 4' }));
        expect(within(list).getByText('New')).toBeTruthy();
        expect(within(list).getByText(/Thin dataset/)).toBeTruthy();
        expect(within(list).getAllByText('Tentative').length).toBe(4);
    });

    /** The ranking's time and the generated time are shown, and the notes. */
    it('shows the ranking times and notes', async () => {
        renderDisplay();
        expect(await screen.findByText(/Ranked for .*computed/)).toBeTruthy();
        expect(screen.getByText('Ranking note.')).toBeTruthy();
    });

    /** "Open in Planner" fires openInPlanner with the target. */
    it('fires openInPlanner', async () => {
        const user = renderDisplay();
        const list = await screen.findByRole('region', { name: 'Imaging candidates' });
        await user.click(within(list).getByRole('button', { name: 'Open in Planner: M 82' }));
        expect(navigateMock).toHaveBeenCalledWith(
            expect.objectContaining({ action: 'openInPlanner', payload: { targetId: 'M 82' }, targetDisplay: 'Observation Manager' }),
        );
    });

    /** "Switch to" makes the candidate the current target. */
    it('switches the current target', async () => {
        const user = renderDisplay();
        const list = await screen.findByRole('region', { name: 'Imaging candidates' });
        await user.click(within(list).getByRole('button', { name: 'Switch to M 82' }));
        const header = screen.getByRole('banner');
        await waitFor(() => expect(within(header).getByText('M 82')).toBeTruthy());
    });

    /** Loading is busy. */
    it('shows the loading state', async () => {
        setBackend({ 'advisory:tonight': () => new Promise(() => undefined) });
        renderDisplay();
        const candidates = await screen.findByRole('region', { name: 'Candidates now' });
        expect(within(candidates).getByText('Loading…').getAttribute('aria-busy')).toBe('true');
    });

    /** A real empty ranking shows the empty state, not example data. */
    it('shows the empty state', async () => {
        setBackend({ 'advisory:tonight': { at: ranking.at, sky: ranking.sky, lists: [] } });
        renderDisplay();
        const candidates = await screen.findByRole('region', { name: 'Candidates now' });
        expect(await within(candidates).findByText(/No candidates yet/)).toBeTruthy();
    });

    /** A failed read shows the error state and Try again reads again. */
    it('shows the error state and retries', async () => {
        setBackend({ 'advisory:tonight': new Error('boom') });
        const user = renderDisplay();
        const candidates = await screen.findByRole('region', { name: 'Candidates now' });
        expect(await within(candidates).findByText('Could not load the candidate ranking')).toBeTruthy();
        setBackend();
        await user.click(within(candidates).getByRole('button', { name: 'Try again' }));
        expect(await screen.findByRole('region', { name: 'Imaging candidates' })).toBeTruthy();
    });
});

describe('an earlier night', () => {
    /** Picking a night shows the stored panels, the no-live-record line and no verdict. */
    it('shows stored panels instead of the live verdict', async () => {
        const user = renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        await user.selectOptions(screen.getByRole('combobox'), '2026-10-23');
        expect(await screen.findByTestId('no-live-record')).toBeTruthy();
        expect(screen.queryByRole('region', { name: 'Live session verdict' })).toBeNull();
        expect(screen.queryByRole('region', { name: 'Candidates now' })).toBeNull();
        expect(callBackendMock).toHaveBeenCalledWith('history:night_quality', { session_id: '2026-10-23' });
        expect(callBackendMock).toHaveBeenCalledWith('history:target_changes', { session_id: '2026-10-23' });
    });

    /** Each target has a row with a badge as returned, a loss account and Open in Planner. */
    it('shows one row per target with its badge and Open in Planner', async () => {
        const user = renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        await user.selectOptions(screen.getByRole('combobox'), '2026-10-23');
        const row = await screen.findByTestId('night-target-M 81');
        expect(within(row).getByText(/1 warning, worst: Warning/)).toBeTruthy();
        expect(within(row).getByText('Captured')).toBeTruthy();
        expect(within(row).getByText('Cause unknown')).toBeTruthy();
        await user.click(within(row).getByRole('button', { name: 'Open in Planner: M 81' }));
        expect(navigateMock).toHaveBeenCalledWith(expect.objectContaining({ action: 'openInPlanner', payload: { targetId: 'M 81' } }));
        expect(screen.getByText('Focus drifted.')).toBeTruthy();
        expect(screen.getByRole('region', { name: 'Night guiding summary' })).toBeTruthy();
    });

    /** The target changes appear as a timeline. */
    it('lists target changes', async () => {
        setBackend({ 'history:target_changes': [{ time: 1761350000, nightId: '2026-10-23', targetId: 'M 82', source: 'ekos-1' }] });
        const user = renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        await user.selectOptions(screen.getByRole('combobox'), '2026-10-23');
        expect(await screen.findByText(/Slewed to M 82/)).toBeTruthy();
    });

    /** A night with no targets shows the empty state. */
    it('shows the empty state for a night with no frames', async () => {
        setBackend({ 'history:night_quality': { ...nightQuality, targets: [], nightStatements: [] } });
        const user = renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        await user.selectOptions(screen.getByRole('combobox'), '2026-10-23');
        expect(await screen.findByText('No frames for this night. Sync to fetch new frames.')).toBeTruthy();
    });

    /** A failed night review shows the error state. */
    it('shows the error state for a failed review', async () => {
        setBackend({ 'history:night_quality': new Error('boom') });
        const user = renderDisplay();
        await screen.findByRole('option', { name: /2026-10-23/ });
        await user.selectOptions(screen.getByRole('combobox'), '2026-10-23');
        expect((await screen.findAllByText('Could not load the quality report')).length).toBeGreaterThan(0);
    });
});

describe('tabs and controls', () => {
    /** The tabs follow the WAI-ARIA pattern: the arrow key moves selection and focus. */
    it('moves between tabs with the arrow keys', async () => {
        const user = renderDisplay();
        const guiding = await screen.findByRole('tab', { name: 'Guiding' });
        expect(guiding.getAttribute('aria-selected')).toBe('true');
        guiding.focus();
        await user.keyboard('{ArrowRight}');
        const control = screen.getByRole('tab', { name: 'Control (Ekos in charge)' });
        expect(control.getAttribute('aria-selected')).toBe('true');
        expect(document.activeElement).toBe(control);
        expect(control.getAttribute('tabindex')).toBe('0');
        expect(guiding.getAttribute('tabindex')).toBe('-1');
    });

    /** The guiding charts have text alternatives. */
    it('gives each guiding chart a text alternative', async () => {
        renderDisplay();
        await screen.findByRole('tab', { name: 'Guiding' });
        expect(screen.getByRole('group', { name: /Line chart of the guiding error/ })).toBeTruthy();
        expect(screen.getByRole('group', { name: /Scatter plot of each guiding correction/ })).toBeTruthy();
    });

    /** Every control is disabled, with the reason as visible text. */
    it('disables every control with a visible reason', async () => {
        const user = renderDisplay();
        await user.click(await screen.findByRole('tab', { name: /Control/ }));
        const panel = screen.getByRole('tabpanel');
        const reason = await within(panel).findByText('Ekos is controlling the equipment. This display only watches.');
        const controls = panel.querySelectorAll('button, input, select');
        expect(controls.length).toBeGreaterThan(20);
        for (const control of Array.from(controls)) {
            expect((control as HTMLButtonElement).disabled, control.outerHTML.slice(0, 80)).toBe(true);
        }
        const slew = within(panel).getByRole('button', { name: 'Slew to Target' });
        expect(slew.getAttribute('aria-describedby')).toBe(reason.id);
        expect(slew.getAttribute('aria-disabled')).toBe('true');
        expect(within(panel).getByRole('button', { name: 'Take test frame' })).toBeTruthy();
        expect(within(panel).getByRole('button', { name: 'Start Imaging Session' })).toBeTruthy();
    });

    /** While the policy is loading the reason says Ekos is being checked. */
    it('says it is checking while the policy loads', async () => {
        setBackend({ 'observatory:safety_status': () => new Promise(() => undefined) });
        const user = renderDisplay();
        await user.click(await screen.findByRole('tab', { name: /Control/ }));
        expect(await screen.findByText('Checking whether Ekos is in control…')).toBeTruthy();
        const panel = screen.getByRole('tabpanel');
        for (const control of Array.from(panel.querySelectorAll('button, input, select'))) {
            expect((control as HTMLButtonElement).disabled).toBe(true);
        }
    });

    /** When the policy read fails the controls stay off. */
    it('keeps the controls off when the policy read fails', async () => {
        setBackend({ 'observatory:safety_status': new Error('down') });
        const user = renderDisplay();
        await user.click(await screen.findByRole('tab', { name: /Control/ }));
        expect(await screen.findByText(/Could not check whether Ekos is in control/)).toBeTruthy();
        expect((screen.getByRole('button', { name: 'Slew to Target' }) as HTMLButtonElement).disabled).toBe(true);
    });
});

describe('registry', () => {
    /** The mode is listed with its own flag, and the flag enables it unless set to "false". */
    it('registers Observation Execution with the enable_execution flag', async () => {
        const { DISPLAY_DEFINITIONS, isDisplayEnabled } = await import('../../common/constants/displayFlags');
        expect(DISPLAY_DEFINITIONS).toContainEqual({ mode: 'Observation Execution', flag: 'enable_execution' });
        expect(isDisplayEnabled({}, 'Observation Execution')).toBe(true);
        expect(isDisplayEnabled({ Frontend: { enable_execution: 'false' } }, 'Observation Execution')).toBe(false);
    });
});
