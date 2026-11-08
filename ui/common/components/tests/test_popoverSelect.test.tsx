/**
 * @file test_popoverSelect.test.tsx
 * @description Tests that PopoverSelect works for any list of choices: it names itself, shows a
 * placeholder, adds an empty choice only when asked, reports the picked id and shows detail text.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '@testing-library/react';
import { PopoverSelect } from '../PopoverSelect';

const cameras = [
    { id: 'asi533', label: 'ASI533MM', detail: 'mono' },
    { id: 'nikon', label: 'Nikon Z6' },
];

describe('PopoverSelect', () => {
    /** With no empty label and nothing picked, the button shows the placeholder and the menu has only the options. */
    it('shows a placeholder and no empty choice by default', async () => {
        const user = userEvent.setup();
        render(<PopoverSelect label="Camera" options={cameras} selectedId={null} onSelect={() => undefined} />);
        const button = screen.getByRole('button', { name: 'Camera: Choose' });
        await user.click(button);
        const menu = screen.getByRole('group', { name: 'Camera' });
        expect(within(menu).getAllByRole('button').map((entry) => entry.textContent)).toEqual(['ASI533MMmono', 'Nikon Z6']);
    });

    /** A picked option is named on the button, and clicking another option reports its id and closes the menu. */
    it('reports the picked id', async () => {
        const user = userEvent.setup();
        const onSelect = vi.fn();
        render(<PopoverSelect label="Camera" options={cameras} selectedId="asi533" onSelect={onSelect} />);
        await user.click(screen.getByRole('button', { name: 'Camera: ASI533MM' }));
        await user.click(screen.getByRole('button', { name: 'Nikon Z6' }));
        expect(onSelect).toHaveBeenCalledWith('nikon');
        expect(screen.queryByRole('group', { name: 'Camera' })).toBeNull();
    });

    /** The empty label adds a first choice that reports null. */
    it('offers an empty choice that reports null when asked', async () => {
        const user = userEvent.setup();
        const onSelect = vi.fn();
        render(<PopoverSelect label="Camera" options={cameras} selectedId="nikon" onSelect={onSelect} emptyLabel="Any camera" />);
        await user.click(screen.getByRole('button', { name: 'Camera: Nikon Z6' }));
        await user.click(screen.getByRole('button', { name: 'Any camera' }));
        expect(onSelect).toHaveBeenCalledWith(null);
    });
});

describe('PopoverSelect options for tight places', () => {
    /** A disabled picker cannot be opened. */
    it('does not open when disabled', async () => {
        const user = userEvent.setup();
        render(<PopoverSelect label="Camera" options={cameras} selectedId="asi533" onSelect={() => undefined} disabled />);
        const button = screen.getByRole('button', { name: 'Camera: ASI533MM' });
        expect((button as HTMLButtonElement).disabled).toBe(true);
        await user.click(button);
        expect(screen.queryByRole('group', { name: 'Camera' })).toBeNull();
    });

    /** The compact size and the open-upward placement are class names on the outer element. */
    it('marks the compact size and the upward placement', () => {
        const { container } = render(
            <PopoverSelect label="Speed" options={cameras} selectedId="nikon" onSelect={() => undefined} size="compact" placement="above" />,
        );
        const outer = container.firstElementChild as HTMLElement;
        expect(outer.classList.contains('popover-select--compact')).toBe(true);
        expect(outer.classList.contains('popover-select--above')).toBe(true);
    });
});
