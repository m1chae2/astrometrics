/**
 * @fileoverview Tests of the Altitude limits and Hour angle limits tabs and the "Why unavailable?"
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

import { LimitSettings, ACTIVE_TELESCOPE_QUERY_KEY, useSafetyLimitsForm } from '../panels/LimitSettings';
import { TelescopeControlPanel } from '../panels/TelescopeControlPanel';
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

/** Shows both limit tabs at once over one shared form, so a test can reach every field. */
const BothGroups: React.FC<{ disabled: boolean; reason: string }> = ({ disabled, reason }) => {
    const form = useSafetyLimitsForm(disabled);
    return (
        <>
            <LimitSettings form={form} group="altitude" disabled={disabled} reason={reason} />
            <LimitSettings form={form} group="hour-angle" disabled={disabled} reason={reason} />
        </>
    );
};

/**
 * Renders the panel inside a fresh query client.
 * @param disabled Whether the app may not command the equipment.
 * @param reason Why the equipment is unavailable.
 * @return The render result and the client.
 */
function renderPanel(disabled = false, reason = '') {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
        <QueryClientProvider client={client}><BothGroups disabled={disabled} reason={reason} /></QueryClientProvider>,
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

describe('Safety limits form', () => {
    /** The saved limits are shown with labels, units and the checkbox state. */
    it('shows the active telescope limits', async () => {
        renderPanel();
        expect(((await screen.findByLabelText('Minimum altitude')) as HTMLInputElement).value).toBe('25');
        expect((screen.getByLabelText('Maximum altitude') as HTMLInputElement).value).toBe('85');
        expect((screen.getByLabelText('Enable altitude limits') as HTMLInputElement).checked).toBe(true);
        expect(screen.getAllByText('Telescope: Refractor').length).toBeGreaterThan(0);
        expect((screen.getByLabelText('Enable hour angle limit') as HTMLInputElement).checked).toBe(false);
        expect((screen.getByLabelText('Meridian flip after transit') as HTMLInputElement).value).toBe('5');
        expect(screen.getByText('hours')).toBeTruthy();
        // The flip field is stored in degrees, so its unit is degrees.
        expect(screen.getByLabelText('Meridian flip after transit').closest('.form-row')?.textContent).toContain('°');
    });

    /** Apply sits at the bottom of the tab body, not in the panel header. */
    it('puts Apply in the tab body', async () => {
        renderPanel();
        await screen.findByLabelText('Minimum altitude');
        const apply = screen.getAllByRole('button', { name: 'Apply safety limits' })[0];
        expect(apply.closest('.limit-settings__footer')).toBeTruthy();
        expect(apply.closest('.panel__header')).toBeNull();
    });

    /** An edit in the Hour angle group counts as a change and is sent. */
    it('enables Apply for an Hour angle edit and sends it', async () => {
        renderPanel();
        fireEvent.change(await screen.findByLabelText('Meridian flip after transit'), { target: { value: '9' } });
        const apply = screen.getAllByRole('button', { name: 'Apply safety limits' })[0] as HTMLButtonElement;
        expect(apply.disabled).toBe(false);
        fireEvent.click(apply);
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('observatory:set_active_telescope',
            expect.objectContaining({ limits: expect.objectContaining({ flip_hour_angle_deg: 9 }) })));
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
        const apply = (await screen.findAllByRole('button', { name: 'Apply safety limits' }))[0] as HTMLButtonElement;
        expect(apply.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText('Minimum altitude'), { target: { value: '30' } });
        expect(screen.getAllByText('Unsaved changes').length).toBeGreaterThan(0);
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
        // The note also stays in the panel as plain text, because the toast fades.
        expect(((await screen.findAllByText(/Ekos's copy of the session may be out of date/))[0]).closest('p')).toBeTruthy();
    });

    /** A refusal stays as visible text until dismissed. */
    it('shows a refusal in an alert until it is dismissed', async () => {
        callBackendMock.mockImplementation(async (method: string) => {
            if (method === 'observatory:get_active_telescope') return savedTelescope;
            throw new Error('Minimum altitude must be below maximum altitude');
        });
        renderPanel();
        fireEvent.change(await screen.findByLabelText('Minimum altitude'), { target: { value: '95' } });
        fireEvent.click(screen.getAllByRole('button', { name: 'Apply safety limits' })[0]);
        const alert = (await screen.findAllByRole('alert'))[0];
        expect(alert.textContent).toContain('must be below maximum');
        expect(emitToastMock).not.toHaveBeenCalled();
        fireEvent.click(screen.getAllByRole('button', { name: 'Dismiss' })[0]);
        expect(screen.queryByRole('alert')).toBeNull();
    });

    /** While commands are off, Apply is greyed even after a change, but inputs stay readable. */
    it('greys Apply while commands are off and keeps inputs readable', async () => {
        renderPanel(true, 'Ekos controls.');
        const apply = (await screen.findAllByRole('button', { name: 'Apply safety limits' }))[0] as HTMLButtonElement;
        expect(apply.disabled).toBe(true);
        expect(apply.getAttribute('aria-disabled')).toBe('true');
        expect(apply.getAttribute('title')).toBe('Ekos controls.');
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

/**
 * Renders the merged Telescope Control panel inside a fresh query client.
 * @param disabled Whether the app may not command the equipment.
 * @return The render result.
 */
function renderMergedPanel(disabled = false) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={client}>
            <TelescopeControlPanel
                isTracking={false}
                isParked={false}
                onSetTracking={vi.fn()}
                onPark={vi.fn()}
                onUnpark={vi.fn()}
                commandsDisabled={disabled}
                commandsDisabledReason={disabled ? 'Night mode.' : ''}
            />
        </QueryClientProvider>,
    );
}

describe('Telescope Control panel tabs', () => {
    /** The five tabs are in the agreed order and Controls is first. */
    it('has Controls, Alignment, Guiding, Altitude and Hour angle tabs in order', () => {
        renderMergedPanel();
        const names = screen.getAllByRole('tab').map((tab) => tab.textContent);
        expect(names).toEqual(['Controls', 'Alignment', 'Guiding', 'Altitude', 'Hour angle']);
        expect(screen.getByRole('tab', { name: 'Controls' }).getAttribute('aria-selected')).toBe('true');
        expect(screen.getByRole('button', { name: 'PARK' })).toBeTruthy();
    });

    /** Each tab shows its own controls, and both limit tabs show the telescope and Apply. */
    it('shows the controls of each tab', async () => {
        renderMergedPanel();
        fireEvent.click(screen.getByRole('tab', { name: 'Alignment' }));
        expect(screen.getByLabelText('Alignment Accuracy')).toBeTruthy();
        fireEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        expect(screen.getByRole('button', { name: 'Start Guiding' })).toBeTruthy();
        fireEvent.click(screen.getByRole('tab', { name: 'Altitude' }));
        expect(await screen.findByLabelText('Minimum altitude')).toBeTruthy();
        expect(screen.queryByLabelText('Maximum hour angle')).toBeNull();
        expect(screen.getByText('Telescope: Refractor')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Apply safety limits' })).toBeTruthy();
        fireEvent.click(screen.getByRole('tab', { name: 'Hour angle' }));
        expect(screen.getByLabelText('Maximum hour angle')).toBeTruthy();
        expect(screen.queryByLabelText('Minimum altitude')).toBeNull();
        expect(screen.getByText('Telescope: Refractor')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Apply safety limits' })).toBeTruthy();
    });

    /** One edit on each tab is kept across tab switches and sent by one Apply. */
    it('shares one form and one Apply between the two limit tabs', async () => {
        renderMergedPanel();
        fireEvent.click(screen.getByRole('tab', { name: 'Altitude' }));
        fireEvent.change(await screen.findByLabelText('Minimum altitude'), { target: { value: '30' } });
        fireEvent.click(screen.getByRole('tab', { name: 'Hour angle' }));
        fireEvent.change(screen.getByLabelText('Meridian flip after transit'), { target: { value: '9' } });
        fireEvent.click(screen.getByRole('tab', { name: 'Controls' }));
        fireEvent.click(screen.getByRole('tab', { name: 'Altitude' }));
        expect((screen.getByLabelText('Minimum altitude') as HTMLInputElement).value).toBe('30');
        fireEvent.click(screen.getByRole('button', { name: 'Apply safety limits' }));
        await waitFor(() => expect(callBackendMock).toHaveBeenCalledWith('observatory:set_active_telescope', {
            telescope_id: 't1',
            limits: expect.objectContaining({ min_altitude_deg: 30, flip_hour_angle_deg: 9 }),
        }));
    });

    /** The Controls tab no longer shows a limits summary line. */
    it('shows no limits summary on the Controls tab', async () => {
        renderMergedPanel();
        await screen.findByRole('button', { name: 'PARK' });
        expect(screen.queryByText(/^Limits:/)).toBeNull();
    });

    /** Night mode (commands off) greys the live controls on every tab. */
    it('greys the live controls on every tab when commands are off', async () => {
        renderMergedPanel(true);
        expect(screen.getAllByRole('button', { name: 'Why unavailable?' })).toHaveLength(1);
        expect((screen.getByRole('button', { name: 'PARK' }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('tab', { name: 'Alignment' }));
        expect((screen.getByLabelText('Alignment Accuracy') as HTMLInputElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('tab', { name: 'Guiding' }));
        expect((screen.getByRole('button', { name: 'Start Guiding' }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('tab', { name: 'Altitude' }));
        expect(((await screen.findByRole('button', { name: 'Apply safety limits' })) as HTMLButtonElement).disabled).toBe(true);
        expect((screen.getByLabelText('Enable altitude limits') as HTMLInputElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('tab', { name: 'Hour angle' }));
        expect((screen.getByLabelText('Enable hour angle limit') as HTMLInputElement).disabled).toBe(true);
        expect((screen.getByRole('button', { name: 'Apply safety limits' }) as HTMLButtonElement).disabled).toBe(true);
    });
});
