/**
 * @fileoverview Tests for the Enhance row: the choices it offers, the save call, the note about
 * when the choice takes effect, and the message when SIMBAD has nothing to list.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BackendError } from '../../common/services/backendApi';
import { BrightObjectRow, BRIGHT_OBJECT_NOTE } from '../components/BrightObjectRow';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../common/services/backendApi')>()),
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

const STACK = '/stacks/M52/M52_L_Stacked.fits';
const bubble = {
    simbad_id: 'NGC  7635', object_type: 'HII', category: 'nebula',
    ra_deg: 350.2, dec_deg: 61.2, major_axis_arcmin: 9.03,
};

/**
 * Answers the three calls the row makes.
 * @param options The saved choice on the target and the list the backend returns.
 */
function answerBackend(options: { saved?: Record<string, unknown>; list?: Record<string, unknown> } = {}) {
    callBackendMock.mockImplementation(async (action: string, params: Record<string, unknown>) => {
        if (action === 'target:get') {
            return [{ id: 'M 52', name: 'M 52', previewBrightObject: options.saved ?? { mode: 'auto' } }];
        }
        if (action === 'processing:list_bright_objects') {
            return options.list ?? { target_id: 'M 52', objects: [bubble], from_cache: false };
        }
        if (action === 'processing:set_bright_object') {
            return { mode: params.mode, simbadId: params.simbad_id ?? null };
        }
        return null;
    });
}

/** Renders the row inside a fresh query client. */
function renderRow() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <BrightObjectRow targetId="M 52" stackPath={STACK} />
        </QueryClientProvider>
    );
}

describe('BrightObjectRow', () => {
    beforeEach(() => {
        callBackendMock.mockReset();
    });

    it('shows Target first and the note about when the choice is used', async () => {
        answerBackend();
        renderRow();
        expect(await screen.findByRole('button', { name: 'Enhance: Target' })).toBeTruthy();
        expect(screen.getByText(BRIGHT_OBJECT_NOTE)).toBeTruthy();
    });

    it('offers Target, Nothing and the objects in the frame', async () => {
        answerBackend();
        renderRow();
        const picker = await screen.findByRole('button', { name: 'Enhance: Target' });
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'processing:list_bright_objects', { target: 'M 52' }, { silent: true }));
        await waitFor(() => fireEvent.click(picker));
        expect(await screen.findByText('Nothing')).toBeTruthy();
        expect(screen.getByText('NGC 7635')).toBeTruthy();
    });

    it('saves a picked object with its position and size', async () => {
        answerBackend();
        renderRow();
        const picker = await screen.findByRole('button', { name: 'Enhance: Target' });
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'processing:list_bright_objects', { target: 'M 52' }, { silent: true }));
        await waitFor(() => fireEvent.click(picker));
        fireEvent.click(await screen.findByText('NGC 7635'));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('processing:set_bright_object', {
            target: 'M 52', mode: 'object', simbad_id: 'NGC  7635', ra_deg: 350.2, dec_deg: 61.2, major_axis_arcmin: 9.03,
        }));
        expect(await screen.findByRole('button', { name: 'Enhance: NGC 7635' })).toBeTruthy();
    });

    it('saves Nothing with no object', async () => {
        answerBackend();
        renderRow();
        fireEvent.click(await screen.findByRole('button', { name: 'Enhance: Target' }));
        fireEvent.click(await screen.findByText('Nothing'));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith(
            'processing:set_bright_object', { target: 'M 52', mode: 'off' }));
    });

    it('shows a saved object that the list no longer holds', async () => {
        answerBackend({
            saved: { mode: 'object', simbadId: 'NGC  7635', raDeg: 350.2, decDeg: 61.2, majorAxisArcmin: 9.03 },
            list: { target_id: 'M 52', objects: [], message: 'SIMBAD could not be reached, so the objects in the frame are not listed.' },
        });
        renderRow();
        expect(await screen.findByRole('button', { name: 'Enhance: NGC 7635' })).toBeTruthy();
        expect(await screen.findByTestId('bright-object-message')).toBeTruthy();
    });

    it('says to run Astrometry first when the stack has no plate solution', async () => {
        answerBackend({ list: { target_id: 'M 52', objects: [], message: 'Run Astrometry first to list objects in the frame' } });
        renderRow();
        expect((await screen.findByTestId('bright-object-message')).textContent)
            .toBe('Run Astrometry first to list objects in the frame');
    });

    it.each([
        ['not_found', 'Restart the app to list objects in the frame.'],
        ['external_service', 'SIMBAD could not be reached, so the objects in the frame are not listed.'],
        ['internal', 'The objects in the frame could not be listed.'],
    ])('explains a %s failure of the list call in plain words, with no console error', async (code, expected) => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        callBackendMock.mockImplementation(async (action: string) => {
            if (action === 'target:get') return [{ id: 'M 52', name: 'M 52', previewBrightObject: { mode: 'auto' } }];
            if (action === 'processing:list_bright_objects') throw new BackendError({ code, message: 'Method not found: raw text' });
            return null;
        });
        renderRow();
        expect((await screen.findByTestId('bright-object-message')).textContent).toBe(expected);
        expect(consoleError).not.toHaveBeenCalled();
        consoleError.mockRestore();
    });

    it('shows nothing without a stack', () => {
        answerBackend();
        const client = new QueryClient();
        const { container } = render(
            <QueryClientProvider client={client}><BrightObjectRow targetId="M 52" /></QueryClientProvider>
        );
        expect(container.firstChild).toBeNull();
    });
});
