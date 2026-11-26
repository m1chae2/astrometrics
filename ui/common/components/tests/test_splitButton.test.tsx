/**
 * @file test_splitButton.test.tsx
 * @description Tests that SplitButton clicks its main action, opens a menu from the arrow, reports the
 * picked id, keeps disabled entries from being picked, turns off while busy and works from the keyboard.
 */

import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';
import { SplitButton, type SplitButtonOption } from '../SplitButton';

const options: SplitButtonOption[] = [
    { id: 'astrometry', label: 'Astrometry' },
    { id: 'photometry', label: 'Photometry', detail: 'runs astrometry first when needed' },
    { id: 'spectroscopy', label: 'Spectroscopy', disabled: true, title: 'No spectral stack' },
];

/**
 * Draws a split button with spy callbacks.
 * @param extra Props to override.
 * @return The spies.
 */
function setup(extra: Partial<React.ComponentProps<typeof SplitButton>> = {}) {
    const onClick = vi.fn();
    const onSelect = vi.fn();
    render(
        <SplitButton label="Analyze" onClick={onClick} options={options} onSelect={onSelect} menuLabel="More options" {...extra} />,
    );
    return { onClick, onSelect };
}

describe('SplitButton', () => {
    /** The main part runs the main action and does not open the menu. */
    it('clicks the main action', async () => {
        const user = userEvent.setup();
        const { onClick } = setup();
        await user.click(screen.getByRole('button', { name: 'Analyze' }));
        expect(onClick).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole('menu')).toBeNull();
    });

    /** The arrow names itself, announces a menu, opens it and reports the picked id. */
    it('opens the menu and reports the picked id', async () => {
        const user = userEvent.setup();
        const { onSelect } = setup();
        const arrow = screen.getByRole('button', { name: 'More options' });
        expect(arrow.getAttribute('aria-haspopup')).toBe('menu');
        expect(arrow.getAttribute('aria-expanded')).toBe('false');
        await user.click(arrow);
        expect(arrow.getAttribute('aria-expanded')).toBe('true');
        expect(screen.getAllByRole('menuitem')).toHaveLength(3);
        await user.click(screen.getByRole('menuitem', { name: /Photometry/ }));
        expect(onSelect).toHaveBeenCalledWith('photometry');
        expect(screen.queryByRole('menu')).toBeNull();
    });

    /** A disabled entry shows its reason as a tooltip and cannot be picked. */
    it('does not pick a disabled entry', async () => {
        const user = userEvent.setup();
        const { onSelect } = setup();
        await user.click(screen.getByRole('button', { name: 'More options' }));
        const disabledItem = screen.getByRole('menuitem', { name: /Spectroscopy/ });
        expect(disabledItem.getAttribute('title')).toBe('No spectral stack');
        expect(disabledItem.getAttribute('aria-disabled')).toBe('true');
        await user.click(disabledItem);
        expect(onSelect).not.toHaveBeenCalled();
    });

    /** Escape closes the menu and puts focus back on the arrow. */
    it('closes on Escape and returns focus to the arrow', async () => {
        const user = userEvent.setup();
        setup();
        const arrow = screen.getByRole('button', { name: 'More options' });
        await user.click(arrow);
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('menu')).toBeNull();
        expect(document.activeElement).toBe(arrow);
    });

    /** A press outside the control closes the menu. */
    it('closes on a press outside', async () => {
        const user = userEvent.setup();
        setup();
        await user.click(screen.getByRole('button', { name: 'More options' }));
        await user.click(document.body);
        expect(screen.queryByRole('menu')).toBeNull();
    });

    /** Down and Up move between enabled entries (skipping disabled ones) and Enter picks the focused one. */
    it('moves with the arrow keys and picks with Enter', async () => {
        const user = userEvent.setup();
        const { onSelect } = setup();
        await user.click(screen.getByRole('button', { name: 'More options' }));
        expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: /Astrometry/ }));
        await user.keyboard('{ArrowDown}');
        expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: /Photometry/ }));
        await user.keyboard('{ArrowDown}');
        expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: /Astrometry/ }));
        await user.keyboard('{ArrowUp}');
        expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: /Photometry/ }));
        await user.keyboard('{Enter}');
        expect(onSelect).toHaveBeenCalledWith('photometry');
    });

    /** While busy, both parts are off and show the busy state. */
    it('turns both parts off while busy', async () => {
        const user = userEvent.setup();
        const { onClick } = setup({ busy: true });
        const main = screen.getByRole('button', { name: 'Analyze' });
        const arrow = screen.getByRole('button', { name: 'More options' });
        expect((main as HTMLButtonElement).disabled).toBe(true);
        expect((arrow as HTMLButtonElement).disabled).toBe(true);
        expect(main.getAttribute('aria-busy')).toBe('true');
        await user.click(main);
        expect(onClick).not.toHaveBeenCalled();
    });

    /** `fill` makes the control span its container and gives the main part the free width. */
    it('fills its container with fill', () => {
        setup({ fill: true });
        const main = screen.getByRole('button', { name: 'Analyze' });
        expect(main.closest('.split-button')?.classList.contains('split-button--fill')).toBe(true);
        expect(main.classList.contains('btn--fit')).toBe(false);
        expect(screen.getByRole('button', { name: 'More options' }).classList.contains('btn--fit')).toBe(true);
    });

    /** The menu is drawn in document.body, outside the control, and presses inside it do not count as outside. */
    it('draws the menu in a portal and keeps it open on an inside press', async () => {
        const user = userEvent.setup();
        const { container } = render(
            <SplitButton label="Analyze" onClick={vi.fn()} options={options} onSelect={vi.fn()} menuLabel="Menu" />,
        );
        await user.click(screen.getByRole('button', { name: 'Menu' }));
        const menu = screen.getByRole('menu');
        expect(container.contains(menu)).toBe(false);
        expect(menu.parentElement).toBe(document.body);
        await user.pointer({ keys: '[MouseLeft>]', target: menu });
        expect(screen.queryByRole('menu')).not.toBeNull();
        await user.click(document.body);
        expect(screen.queryByRole('menu')).toBeNull();
    });

    describe('placement', () => {
        const originalHeight = window.innerHeight;
        afterEach(() => {
            Object.defineProperty(window, 'innerHeight', { value: originalHeight, configurable: true });
            vi.restoreAllMocks();
        });

        /** Draws the control at a set box and a 100px tall menu, in a window of the given height. */
        async function openAt(rect: Partial<DOMRect>, windowHeight: number) {
            Object.defineProperty(window, 'innerHeight', { value: windowHeight, configurable: true });
            vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
                { left: 20, right: 220, width: 200, height: 30, x: 20, y: 0, toJSON: () => ({}), top: 0, bottom: 0, ...rect } as DOMRect,
            );
            vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(100);
            const user = userEvent.setup();
            setup();
            await user.click(screen.getByRole('button', { name: 'More options' }));
            return screen.getByRole('menu');
        }

        /** With room below, the menu sits under the control and is at least as wide as it. */
        it('opens below with the control width', async () => {
            const menu = await openAt({ top: 100, bottom: 130 }, 800);
            expect(menu.style.top).toBe('138px');
            expect(menu.style.left).toBe('20px');
            expect(menu.style.minWidth).toBe('200px');
        });

        /** Near the bottom of the window, the menu flips above the control. */
        it('flips above when there is no room below', async () => {
            const menu = await openAt({ top: 700, bottom: 730 }, 760);
            expect(menu.style.top).toBe('592px');
        });
    });
});
