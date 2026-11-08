/**
 * @fileoverview Tests that a Telescope Control button stops showing "pending" when its command
 * fails or does nothing, and after a time limit when the telescope state never changes.
 */

import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { TelescopeControl, PENDING_ACTION_TIMEOUT_MS } from '../panels/TelescopeControl';

/**
 * Renders the panel with the given alignment callback.
 * @param onStartAlignment The Start Alignment command.
 * @return The render result.
 */
function renderPanel(onStartAlignment: () => Promise<unknown> | void) {
    return render(
        <TelescopeControl
            isTracking={false}
            isParked={false}
            onSetTracking={vi.fn()}
            onPark={vi.fn()}
            onUnpark={vi.fn()}
            onStartAlignment={onStartAlignment}
        />,
    );
}

/** Tells whether the Start Alignment button shows the pending state. */
const isPending = (): boolean => screen.getByRole('button', { name: /Start Alignment/ }).className.includes('pending')
    || screen.getByRole('button', { name: /Start Alignment/ }).getAttribute('aria-busy') === 'true';

afterEach(() => { vi.useRealTimers(); });

describe('TelescopeControl pending state', () => {
    /** A command that resolves to false clears the pending state. */
    it('clears pending when the command returns false', async () => {
        renderPanel(async () => false);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Start Alignment/ })); });
        expect(isPending()).toBe(false);
    });

    /** A command that rejects clears the pending state. */
    it('clears pending when the command fails', async () => {
        renderPanel(async () => { throw new Error('no'); });
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Start Alignment/ })); });
        expect(isPending()).toBe(false);
    });

    /** An accepted command stays pending until the state flips, then the time limit ends it. */
    it('stays pending after success, and clears after the time limit', async () => {
        vi.useFakeTimers();
        renderPanel(async () => true);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Start Alignment/ })); });
        expect(isPending()).toBe(true);
        await act(async () => { vi.advanceTimersByTime(PENDING_ACTION_TIMEOUT_MS + 10); });
        expect(isPending()).toBe(false);
    });
});
