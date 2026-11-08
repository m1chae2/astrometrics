/**
 * @file test_sessionPicker.test.tsx
 * @description Tests of the shared session picker: it shows the live choice and the given
 * sessions, names itself for screen readers, reports the picked id (or null for live),
 * shows the detail text, closes on Escape with focus back on its button, and treats an
 * unknown id as the live choice.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '@testing-library/react';
import { SessionPicker } from '../SessionPicker';

const options = [
    { id: '2026-11-04', label: '2026-11-04', detail: 'M 13, M 57' },
    { id: '2026-11-03', label: '2026-11-03' },
];

describe('SessionPicker', () => {
    /** The button names the picker and the picked entry; the menu lists Live first, then the sessions with details. */
    it('shows the live choice first, then the sessions with their details', async () => {
        const user = userEvent.setup();
        render(<SessionPicker label="Night" options={options} selectedId={null} onSelect={() => undefined} />);
        const button = screen.getByRole('button', { name: 'Night: Live' });
        expect(button.getAttribute('aria-expanded')).toBe('false');
        expect(screen.queryByRole('group', { name: 'Night' })).toBeNull();
        await user.click(button);
        expect(button.getAttribute('aria-expanded')).toBe('true');
        const menu = screen.getByRole('group', { name: 'Night' });
        expect(within(menu).getAllByRole('button').map((entry) => entry.textContent)).toEqual([
            'Live', '2026-11-04M 13, M 57', '2026-11-03',
        ]);
        expect(within(menu).getByRole('button', { name: 'Live' }).getAttribute('aria-pressed')).toBe('true');
    });

    /** Picking a session reports its id and closes the menu; picking Live reports null. */
    it('reports the picked id, and null for the live choice', async () => {
        const user = userEvent.setup();
        const onSelect = vi.fn();
        render(<SessionPicker label="Night" options={options} selectedId="2026-11-04" onSelect={onSelect} />);
        expect(screen.getByRole('button', { name: 'Night: 2026-11-04' })).toBeTruthy();
        await user.click(screen.getByRole('button', { name: 'Night: 2026-11-04' }));
        await user.click(screen.getByRole('button', { name: '2026-11-03' }));
        expect(onSelect).toHaveBeenLastCalledWith('2026-11-03');
        expect(screen.queryByRole('group', { name: 'Night' })).toBeNull();
        await user.click(screen.getByRole('button', { name: 'Night: 2026-11-04' }));
        await user.click(screen.getByRole('button', { name: 'Live' }));
        expect(onSelect).toHaveBeenLastCalledWith(null);
    });

    /** The keyboard works: Enter opens the menu, Escape closes it and focus returns to the button. */
    it('opens from the keyboard and closes on Escape with focus back on the button', async () => {
        const user = userEvent.setup();
        render(<SessionPicker label="Night" options={options} selectedId={null} onSelect={() => undefined} />);
        const button = screen.getByRole('button', { name: 'Night: Live' });
        button.focus();
        await user.keyboard('{Enter}');
        const menu = screen.getByRole('group', { name: 'Night' });
        await user.tab();
        expect(menu.contains(document.activeElement)).toBe(true);
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('group', { name: 'Night' })).toBeNull();
        expect(document.activeElement).toBe(button);
    });

    /** The live text can be changed, and an id that is not listed reads as the live choice. */
    it('uses the given live text and treats an unknown id as live', () => {
        render(<SessionPicker label="Session" liveLabel="No Session Selected" options={options} selectedId="gone" onSelect={() => undefined} />);
        expect(screen.getByRole('button', { name: 'Session: No Session Selected' })).toBeTruthy();
    });
});
