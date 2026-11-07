/**
 * @fileoverview Tests of the Health column of the calibration library panel:
 * the health of each set is read apart from the counts, joined by row key,
 * worded in plain words, and "Not checked" is told from "OK".
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { CalibrationSetHealth } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/components/IngestFramesModal', () => ({ IngestFramesModal: () => null }));
vi.mock('../../common/hooks/useIngestionManager', () => ({ useIngestionManager: () => ({ isActive: false }) }));

import { socketClient } from '../../common/utils/socketClient';
import { CalibrationLibraryPanel, formatTemperature } from '../calibration/CalibrationLibraryPanel';
import { healthByRowKey, healthWord, worstHealth } from '../calibration/calibrationHealth';

/**
 * Sends an action to every listener the socket client holds.
 * @param action The event name.
 * @param payload The event payload.
 */
function sendAction(action: string, payload: unknown): void {
    (socketClient as unknown as { emit: (name: string, ...args: unknown[]) => void }).emit('action', action, payload);
}

const CAMERA = 'ZWO ASI533MM Pro';

const libraryReply = {
    detail: 'counts',
    darks: [
        { row_key: 'dark-ok', camera: CAMERA, iso: '0', offset: 10, binning: '1x1', temperature_c: -9, exposure: 120, count: 30 },
        { row_key: 'dark-bad', camera: CAMERA, iso: '0', offset: 0, binning: '1x1', temperature_c: -9, exposure: 300, count: 30 },
        { row_key: 'dark-new', camera: CAMERA, iso: '0', offset: 10, binning: '1x1', temperature_c: null, exposure: 2, count: 9 },
    ],
    biases: [{ row_key: 'bias-a', camera: CAMERA, iso: '0', offset: 10, binning: '1x1', count: 20 }],
    flats: [{ row_key: 'flat-a', camera: CAMERA, filter: 'Luminance', iso: '0', offset: 10, binning: '1x1', count: 12 }],
    camera_totals: [{ camera: CAMERA, dark_count: 69, bias_count: 20, flat_count: 12 }],
};

/**
 * Builds one health record.
 * @param rowKey The key that joins it to a row.
 * @param status good, warning or not_checked.
 * @param summary The one-sentence reason.
 * @param extra Other fields to set.
 * @return The record.
 */
function health(rowKey: string, status: string, summary: string, extra: Partial<CalibrationSetHealth> = {}): CalibrationSetHealth {
    return {
        kind: 'dark',
        rowKey,
        camera: CAMERA,
        gain: '0',
        offset: 10,
        frameCount: 30,
        uniqueFrameCount: 30,
        status,
        summary,
        checks: [],
        ...extra,
    } as CalibrationSetHealth;
}

const darkHealth = [
    health('dark-ok', 'good', 'No problem found.'),
    health('dark-bad', 'warning', 'dark frames are cut off at zero: 96% of the pixels read exactly zero.', {
        checks: [
            { name: 'dark_zero_pixel_fraction', status: 'failed', detail: 'use a camera offset above 0', measuredValue: 0.96 },
            { name: 'calibration_frame_count', status: 'passed' },
        ] as CalibrationSetHealth['checks'],
        framesOfConcern: [{ path: '/lib/darks/Dark_001.fits', reason: '96% of its pixels are exactly zero' }],
    }),
    health('dark-new', 'not_checked', 'none of the 9 dark frame(s) has been measured yet; start the measuring job'),
];

/**
 * Renders the panel in a fresh query client.
 * @return The render result.
 */
function renderPanel() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <CalibrationLibraryPanel />
        </QueryClientProvider>,
    );
}

/**
 * Answers the panel's backend calls.
 * @param method The RPC method.
 * @param params Its parameters.
 * @return The canned reply.
 */
async function answer(method: string, params: Record<string, unknown>): Promise<unknown> {
    if (method === 'calibration:query') return libraryReply;
    if (method === 'calibration:assess') {
        if (params.kind === 'dark') return darkHealth;
        if (params.kind === 'bias') return [health('bias-a', 'good', 'No problem found.', { kind: 'bias' })];
        return [health('flat-a', 'warning', 'flats are faint', { kind: 'flat', telescope: 'Scope A' }), health('flat-a', 'good', 'No problem found.', { kind: 'flat', telescope: 'Scope B' })];
    }
    throw new Error(`unexpected ${method}`);
}

beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockImplementation((method: string, params: Record<string, unknown>) => answer(method, params));
});

describe('the Health column of the Darks table', () => {
    /** Each row gets the health with the same row key, in words as well as a shape. */
    it('joins the health to the rows by row key and says it in words', async () => {
        renderPanel();
        const table = await screen.findByRole('table', { name: 'Darks' });
        await waitFor(() => expect(within(table).getByText('Warning')).toBeTruthy());
        expect(within(table).getByText('OK')).toBeTruthy();
        expect(within(table).getByText('Not checked')).toBeTruthy();
        expect(callBackendMock).toHaveBeenCalledWith('calibration:assess', { kind: 'dark', camera_id: CAMERA });
    });

    /** Before the health is read, the cell is a dash and not a pass. */
    it('shows a dash before any check has answered', async () => {
        callBackendMock.mockImplementation((method: string, params: Record<string, unknown>) =>
            method === 'calibration:assess' ? new Promise(() => undefined) : answer(method, params),
        );
        renderPanel();
        const table = await screen.findByRole('table', { name: 'Darks' });
        expect(within(table).queryByText('OK')).toBeNull();
        expect(within(table).getAllByLabelText('Not yet checked')).toHaveLength(3);
    });

    /** Choosing a set shows its reason, each check and the files to look at in plain words. */
    it('shows the plain reason and the files of concern when a set is chosen', async () => {
        renderPanel();
        const table = await screen.findByRole('table', { name: 'Darks' });
        await waitFor(() => expect(within(table).getByText('Warning')).toBeTruthy());
        await userEvent.click(within(table).getByRole('button', { name: /Warning/ }));
        const detail = await screen.findByTestId('calibration-health-detail');
        expect(detail.textContent).toContain('cut off at zero');
        expect(detail.textContent).toContain('Cut off at zero — Failed: use a camera offset above 0');
        expect(detail.textContent).toContain('Number of frames — Passed');
        expect(detail.textContent).toContain('Dark_001.fits');
    });

    /** A set that could not be checked says why, apart from a pass. */
    it('shows the reason of a set that is not checked', async () => {
        renderPanel();
        const table = await screen.findByRole('table', { name: 'Darks' });
        await waitFor(() => expect(within(table).getByText('Not checked')).toBeTruthy());
        await userEvent.click(within(table).getByRole('button', { name: /Not checked/ }));
        expect((await screen.findByTestId('calibration-health-detail')).textContent).toContain('start the measuring job');
    });
});

describe('the Biases and Flats tables', () => {
    /** The Biases tab has Gain, Offset, Count and Health. */
    it('shows the Biases columns and its health', async () => {
        renderPanel();
        await screen.findByRole('table', { name: 'Darks' });
        await userEvent.click(screen.getByRole('tab', { name: /^Biases/ }));
        const table = await screen.findByRole('table', { name: 'Biases' });
        expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Gain', 'Offset', 'Count', 'Health']);
        await waitFor(() => expect(within(table).getByText('OK')).toBeTruthy());
        expect(callBackendMock).toHaveBeenCalledWith('calibration:assess', { kind: 'bias', camera_id: CAMERA });
    });

    /** Two flat sets that share a row key show the worse of the two. */
    it('shows the worst health when two telescopes share a flat row', async () => {
        renderPanel();
        await screen.findByRole('table', { name: 'Darks' });
        await userEvent.click(screen.getByRole('tab', { name: /^Flats/ }));
        const table = await screen.findByRole('table', { name: 'Flats' });
        expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Filter', 'Gain', 'Offset', 'Count', 'Health']);
        await waitFor(() => expect(within(table).getByText('Warning')).toBeTruthy());
        expect(within(table).queryByText('OK')).toBeNull();
    });
});

describe('the measuring of new dark and bias frames', () => {
    /** The backend measures by itself, so the panel has no Check sets button. */
    it('has no Check sets button', async () => {
        renderPanel();
        await screen.findByRole('table', { name: 'Darks' });
        expect(screen.queryByRole('button', { name: 'Check sets' })).toBeNull();
        expect(callBackendMock).not.toHaveBeenCalledWith('calibration:measure_frames', expect.anything());
    });

    /** The calibration:measured event makes the panel read the health again. */
    it('reads the health again when the measuring ends', async () => {
        renderPanel();
        await screen.findByRole('table', { name: 'Darks' });
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('calibration:assess', expect.anything()));
        const assessCalls = () => callBackendMock.mock.calls.filter(([method]) => method === 'calibration:assess').length;
        const before = assessCalls();

        act(() => sendAction('quality:measured', {}));
        expect(assessCalls()).toBe(before);

        act(() => sendAction('calibration:measured', {}));
        await waitFor(() => expect(assessCalls()).toBeGreaterThan(before));
    });
});

describe('the health helpers', () => {
    /** Each status has its own word, and no status reads as a pass. */
    it('words the three statuses and treats anything else as not checked', () => {
        expect(healthWord('good').word).toBe('OK');
        expect(healthWord('warning').word).toBe('Warning');
        expect(healthWord('not_checked').word).toBe('Not checked');
        expect(healthWord(undefined).word).toBe('Not checked');
    });

    /** The worst of several records is a warning, then OK, then not checked. */
    it('picks the worst record', () => {
        const worst = worstHealth([health('k', 'not_checked', 'a'), health('k', 'good', 'b'), health('k', 'warning', 'c')]);
        expect(worst?.summary).toBe('c');
        expect(worstHealth([])).toBeUndefined();
    });

    /** An answer that is not a list joins to nothing, instead of failing. */
    it('treats an answer that is not a list as empty', () => {
        expect(healthByRowKey({ detail: 'counts' }).size).toBe(0);
        expect(healthByRowKey(null).size).toBe(0);
        expect(healthByRowKey([health('a', 'good', 'x')]).get('a')?.summary).toBe('x');
    });

    /** A temperature slot is shown with its unit, and a missing one as a dash. */
    it('formats the temperature of a dark set', () => {
        expect(formatTemperature(-9)).toBe('-9 °C');
        expect(formatTemperature(null)).toBe('—');
    });
});
