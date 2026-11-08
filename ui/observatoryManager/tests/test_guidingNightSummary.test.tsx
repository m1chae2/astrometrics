/**
 * @file test_guidingNightSummary.test.tsx
 * @description Tests of the Guiding Trends panel with a night chosen at the top of the view:
 * Live shows the plot, a night shows its summary (numbers, statements, runs), a night with no
 * summary shows the backend's reason, and the panel no longer has a picker of its own.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/components/plotting/TimeSeriesPlot', () => ({ TimeSeriesPlot: () => <div>live plot</div> }));
vi.mock('../../common/components/plotting/ScatterPlot', () => ({ ScatterPlot: () => <div>scatter</div> }));

import { GuidingTrendsPlot } from '../panels/GuidingStatusPanel';
import { ObservatoryNightContext, liveOnlyReasonForNight, LIVE_NIGHT_VALUE_OBJECT } from '../ObservatoryNightContext';

const liveSample = { time: 1, dra: 0.1, ddec: 0.2, snr: 30, rmsRa: 0.3, rmsDec: 0.3, pulseRa: 0, pulseDec: 0 };

/**
 * Renders the panel for Live or for one night.
 * @param nightId The night, or null for Live.
 * @return The render result.
 */
function renderPanel(nightId: string | null, history: unknown[] = [liveSample]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const value = nightId === null
        ? LIVE_NIGHT_VALUE_OBJECT
        : { isLive: false, nightId, nightDate: nightId, night: null, liveOnlyReason: liveOnlyReasonForNight(nightId) };
    return render(
        <QueryClientProvider client={client}>
            <ObservatoryNightContext.Provider value={value}>
                <GuidingTrendsPlot history={history as never[]} />
            </ObservatoryNightContext.Provider>
        </QueryClientProvider>,
    );
}

/**
 * Makes the backend answer a night review.
 * @param guiding The guiding summary the review returns.
 */
function answerWith(guiding: Record<string, unknown>) {
    callBackendMock.mockImplementation((method: string) => {
        if (method === 'history:night_quality') return Promise.resolve({ nightId: '2026-11-04', guiding });
        return Promise.resolve(null);
    });
}

describe('Guiding Trends for a chosen night', () => {
    beforeEach(() => callBackendMock.mockReset());

    /** Live shows the plot and its controls, and the panel has no Night picker of its own. */
    it('shows the live plot on Live', () => {
        renderPanel(null);
        expect(screen.getByText('live plot')).toBeTruthy();
        expect(screen.getByText('History:')).toBeTruthy();
        expect(screen.queryByLabelText('Night:')).toBeNull();
        expect(callBackendMock).not.toHaveBeenCalled();
    });

    /** A night is read by id; its numbers, statements and runs are shown in place of the plot. */
    it('shows the summary of the chosen night', async () => {
        answerWith({
            available: true,
            rmsPerAxisArcsec: 0.85,
            expectedStarWideningFraction: 0.052,
            lostFraction: 0.013,
            excursionCount: 4,
            statements: [{ rule: 'r1', message: 'raw', plainMessage: 'Guiding was steady.' }],
            runs: [{ runId: 'a', startedAt: 1_762_300_000, durationSeconds: 2520, rmsPerAxisArcsec: 0.9, altitudeDegrees: 55.4, pierSide: 'East' }],
        });
        renderPanel('2026-11-04');
        expect(await screen.findByText('0.85 arcsec')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('history:night_quality', { session_id: '2026-11-04' });
        expect(screen.getByText('5.2%')).toBeTruthy();
        expect(screen.getByText('1.3%')).toBeTruthy();
        expect(screen.getByText('4')).toBeTruthy();
        expect(screen.getByText('Guiding was steady.')).toBeTruthy();
        const table = screen.getByRole('table');
        expect(within(table).getAllByRole('columnheader').map((header) => header.textContent))
            .toEqual(['Start', 'Length', 'RMS (arcsec)', 'Altitude', 'Pier side']);
        expect(within(table).getByText('42 min')).toBeTruthy();
        expect(within(table).getByText('East')).toBeTruthy();
        expect(screen.queryByText('live plot')).toBeNull();
        expect(screen.queryByText('History:')).toBeNull();
    });

    /** A night with no summary shows the reason the backend gives. */
    it('shows the reason when the night has no guiding summary', async () => {
        answerWith({ available: false, reason: 'No guiding log was recorded.' });
        renderPanel('2026-11-02');
        expect(await screen.findByText('No guiding log was recorded.')).toBeTruthy();
    });

    /** With no live samples the panel shows the newest night that has guiding data. */
    it('shows the last night with guiding when there are no live samples', async () => {
        callBackendMock.mockImplementation((method: string) => {
            if (method === 'history:nights') {
                return Promise.resolve([
                    { nightId: '2026-11-05', nightDate: '2026-11-05', hasGuidingData: false },
                    { nightId: '2026-11-04', nightDate: '2026-11-04', hasGuidingData: true },
                    { nightId: '2026-11-02', nightDate: '2026-11-02', hasGuidingData: true },
                ]);
            }
            if (method === 'history:night_quality') {
                return Promise.resolve({ nightId: '2026-11-04', guiding: { available: true, rmsPerAxisArcsec: 0.7, statements: [], runs: [] } });
            }
            return Promise.resolve(null);
        });
        const view = renderPanel(null, []);
        expect(await screen.findByText('Last night with guiding: 2026-11-04')).toBeTruthy();
        expect(await screen.findByText('0.70 arcsec')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('history:night_quality', { session_id: '2026-11-04' });
        expect(screen.queryByText('No guiding data yet. The trends appear once guiding starts.')).toBeNull();
        // Live samples arrive: back to the live plot.
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        view.rerender(
            <QueryClientProvider client={client}>
                <ObservatoryNightContext.Provider value={LIVE_NIGHT_VALUE_OBJECT}>
                    <GuidingTrendsPlot history={[liveSample as never]} />
                </ObservatoryNightContext.Provider>
            </QueryClientProvider>,
        );
        expect(await screen.findByText('live plot')).toBeTruthy();
        expect(screen.getByText('Guiding Trends')).toBeTruthy();
        expect(screen.queryByText(/Last night with guiding/)).toBeNull();
    });

    /** With no live samples and no night with guiding data, the old message stays. */
    it('keeps the message when no night has guiding data', async () => {
        callBackendMock.mockImplementation((method: string) =>
            Promise.resolve(method === 'history:nights' ? [{ nightId: 'n', nightDate: '2026-11-03', hasGuidingData: false }] : null));
        renderPanel(null, []);
        expect(await screen.findByText('No guiding data yet. The trends appear once guiding starts.')).toBeTruthy();
    });
});
