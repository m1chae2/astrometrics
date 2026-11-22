/**
 * @file test_statusChipDetails.test.tsx
 * @description Tests the `details` popover of StatusChip: hidden at rest, shown on hover and on
 * keyboard focus, closed by Escape, and absent (no extra tab stop) when no details are given.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StatusChip } from '../StatusChip';

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
