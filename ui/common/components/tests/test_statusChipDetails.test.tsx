/**
 * @file test_statusChipDetails.test.tsx
 * @description Tests the `details` popover of StatusChip: hidden at rest, shown on hover and on
 * keyboard focus, closed by Escape, and absent (no extra tab stop) when no details are given.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StatusChip } from '../StatusChip';

/** Makes the chip sit at a rectangle and the popover have a size, as a browser would measure them. */
const mockLayout = (
    chip: { left: number; top: number; bottom: number },
    popover: { width: number; height: number },
): void => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
        left: chip.left, top: chip.top, bottom: chip.bottom, right: chip.left + 40, width: 40,
        height: chip.bottom - chip.top, x: chip.left, y: chip.top, toJSON: () => ({}),
    });
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(popover.width);
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(popover.height);
};

describe('StatusChip details placement', () => {
    afterEach(() => vi.restoreAllMocks());

    const open = (): HTMLElement => {
        const { container } = render(<StatusChip status="ok" label="Complete" details={<p>More</p>} />);
        fireEvent.mouseEnter(container.querySelector('[tabindex="0"]') as HTMLElement);
        return screen.getByRole('tooltip');
    };

    it('shifts left so a wide popover stays inside the window', () => {
        mockLayout({ left: window.innerWidth - 50, top: 100, bottom: 120 }, { width: 400, height: 100 });
        const style = open().style;
        expect(parseFloat(style.left) + 400).toBeLessThanOrEqual(window.innerWidth - 8);
        expect(parseFloat(style.top)).toBe(124);
    });

    it('flips above the chip when there is more room above', () => {
        mockLayout({ left: 20, top: window.innerHeight - 40, bottom: window.innerHeight - 20 }, { width: 200, height: 150 });
        const style = open().style;
        expect(parseFloat(style.top)).toBe(window.innerHeight - 40 - 4 - 150);
    });

    it('limits its height and scrolls when taller than the room', () => {
        mockLayout({ left: 20, top: 300, bottom: 320 }, { width: 200, height: 5000 });
        const style = open().style;
        expect(parseFloat(style.maxHeight)).toBeLessThanOrEqual(window.innerHeight - 8);
        expect(parseFloat(style.top)).toBeGreaterThanOrEqual(8);
    });
});

describe('StatusChip details', () => {
    it('shows the details on hover and hides them on leave', () => {
        const { container } = render(<StatusChip status="ok" label="Complete" details={<p>More</p>} />);
        expect(screen.queryByRole('tooltip')).toBeNull();
        const trigger = container.querySelector('[tabindex="0"]') as HTMLElement;
        fireEvent.mouseEnter(trigger);
        expect(screen.getByRole('tooltip').textContent).toBe('More');
        fireEvent.mouseLeave(trigger);
        expect(screen.queryByRole('tooltip')).toBeNull();
    });

    it('shows the details on focus and closes them on Escape', () => {
        const { container } = render(<StatusChip status="warning" label="Check" details={<p>More</p>} />);
        const trigger = container.querySelector('[tabindex="0"]') as HTMLElement;
        fireEvent.focus(trigger);
        expect(screen.getByRole('tooltip')).toBeTruthy();
        fireEvent.keyDown(trigger, { key: 'Escape' });
        expect(screen.queryByRole('tooltip')).toBeNull();
    });

    it('is not focusable without details', () => {
        const { container } = render(<StatusChip status="ok" label="Ready" />);
        expect(container.querySelector('[tabindex]')).toBeNull();
    });
});
