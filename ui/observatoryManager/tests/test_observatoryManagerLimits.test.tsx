/**
 * @fileoverview Tests of the Safety Limits panel and the "Why unavailable?"
 * buttons: the real limits are read and shown, the form resets when a new read
 * arrives, Apply is enabled only after a change and sends the right payload,
 * refusals stay visible until dismissed, success warns about Ekos's copy,
 * Apply is greyed while commands are off, and greyed inputs stay readable.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const callBackendMock = vi.fn();
const emitToastMock = vi.fn();

vi.mock('../../common/services/backendApi', () => ({
    callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/utils/emitToast', () => ({
    emitToast: (...args: unknown[]) => emitToastMock(...args),
}));

import { LimitSettings, ACTIVE_TELESCOPE_QUERY_KEY } from '../panels/LimitSettings';
import { CommandGate, WhyUnavailableButton } from '../CommandGate';

const savedTelescope = {
    id: 't1',
    name: 'Refractor',
    focal_length_mm: 500,
    altitude_limits_enabled: true,
    min_altitude_deg: 25,
    max_altitude_deg: 85,
    hour_angle_limits_enabled: false,
    max_hour_angle_hours: 3,
    flip_hour_angle_deg: 5,
};

/**
 * Renders the panel inside a fresh query client.
 * @param disabled Whether the app may not command the equipment.
 * @return The render result and the client.
 */
function renderPanel(disabled = false) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
        <QueryClientProvider client={client}><LimitSettings disabled={disabled} /></QueryClientProvider>,
    );
    return { client, ...view };
}

beforeEach(() => {
    callBackendMock.mockReset();
    emitToastMock.mockReset();
    callBackendMock.mockImplementation(async (method: string) => {
        if (method === 'observatory:get_active_telescope') return savedTelescope;
        if (method === 'observatory:set_active_telescope') return true;
        throw new Error(`unexpected ${method}`);
    });
});

describe('Safety Limits panel', () => {
    /** The saved limits are shown with labels, units and the checkbox state. */
    it('shows the active telescope limits', async () => {
        renderPanel();
        expect(((await screen.findByLabelText('Minimum altitude')) as HTMLInputElement).value).toBe('25');
        expect((screen.getByLabelText('Maximum altitude') as HTMLInputElement).value).toBe('85');
        expect((screen.getByLabelText('Enable altitude limits') as HTMLInputElement).checked).toBe(true);
        expect((screen.getByLabelText('Enable hour angle limit') as HTMLInputElement).checked).toBe(false);
        expect((screen.getByLabelText('Meridian flip after (hour angle)') as HTMLInputElement).value).toBe('5');
        expect(screen.getByText('hours')).toBeTruthy();
    });

    /** A new read replaces what the user typed. */
    it('resets the form when a new read arrives', async () => {
        const { client } = renderPanel();
        const minimum = (await screen.findByLabelText('Minimum altitude')) as HTMLInputElement;
        fireEvent.change(minimum, { target: { value: '40' } });
        expect(minimum.value).toBe('40');
        client.setQueryData(ACTIVE_TELESCOPE_QUERY_KEY, { ...savedTelescope, min_altitude_deg: 30 });
        await waitFor(() => expect(minimum.value).toBe('30'));
    });

    /** Apply waits for a change, then sends the full limits and warns about Ekos. */
    it('enables Apply after a change, sends the payload and shows a success toast', async () => {
        renderPanel();
        const apply = (await screen.findByRole('button', { name: 'Apply safety limits' })) as HTMLButtonElement;
        expect(apply.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText('Minimum altitude'), { target: { value: '30' } });
        expect(screen.getByText('Unsaved changes')).toBeTruthy();
        expect(apply.disabled).toBe(false);
        fireEvent.click(apply);
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('observatory:set_active_telescope', {
            telescope_id: 't1',
            limits: {
                altitude_limits_enabled: true,
                min_altitude_deg: 30,
                max_altitude_deg: 85,
                hour_angle_limits_enabled: false,
                max_hour_angle_hours: 3,
                flip_hour_angle_deg: 5,
            },
        }));
        await waitFor(() => expect(emitToastMock).toHaveBeenCalled());
        expect(String(emitToastMock.mock.calls[0][0])).toContain("Ekos's copy of the session may be out of date");
        expect(emitToastMock.mock.calls[0][1]).toBe('success');
    });

    /** A refusal stays as visible text until dismissed. */
    it('shows a refusal in an alert until it is dismissed', async () => {
        callBackendMock.mockImplementation(async (method: string) => {
            if (method === 'observatory:get_active_telescope') return savedTelescope;
            throw new Error('Minimum altitude must be below maximum altitude');
        });
        renderPanel();
        fireEvent.change(await screen.findByLabelText('Minimum altitude'), { target: { value: '95' } });
        fireEvent.click(screen.getByRole('button', { name: 'Apply safety limits' }));
        const alert = await screen.findByRole('alert');
        expect(alert.textContent).toContain('must be below maximum');
        expect(emitToastMock).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
        expect(screen.queryByRole('alert')).toBeNull();
    });

    /** While commands are off, Apply is greyed even after a change, but inputs stay readable. */
    it('greys Apply while commands are off and keeps inputs readable', async () => {
        renderPanel(true);
        const apply = (await screen.findByRole('button', { name: 'Apply safety limits' })) as HTMLButtonElement;
        expect(apply.disabled).toBe(true);
        expect(apply.getAttribute('aria-disabled')).toBe('true');
        expect((screen.getByLabelText('Minimum altitude') as HTMLInputElement).value).toBe('25');
    });
});

describe('Why unavailable button', () => {
    /** The button is reachable by keyboard and shows the reason. */
    it('shows the reason on keyboard focus', async () => {
        render(<WhyUnavailableButton disabled={true} reason="Ekos controls the equipment." />);
        await userEvent.tab();
        const button = screen.getByRole('button', { name: 'Why unavailable?' });
        expect(document.activeElement).toBe(button);
        expect(screen.getByRole('note').textContent).toBe('Ekos controls the equipment.');
    });

    /** Nothing is drawn while the controls are available. */
    it('is not drawn while the controls are available', () => {
        render(<WhyUnavailableButton disabled={false} reason="x" />);
        expect(screen.queryByRole('button')).toBeNull();
    });

    /** The gate's stylesheet rule dims only buttons and toggles, not inputs. */
    it('keeps disabled inputs undimmed in the stylesheet', async () => {
        const fs = await import('node:fs');
        const css = fs.readFileSync('ui/observatoryManager/observatoryManager.css', 'utf8');
        const rule = css.split('\n').find((line) => line.includes('opacity: 0.4')) ?? '';
        expect(rule).toContain("input[type='checkbox']");
        expect(rule).not.toMatch(/input[,:]/);
        render(<CommandGate disabled={true} reason="r"><input readOnly value="1" /></CommandGate>);
    });
});
