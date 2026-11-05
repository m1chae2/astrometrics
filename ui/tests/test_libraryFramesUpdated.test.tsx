/**
 * @fileoverview Tests that the `library:frames_updated` event, sent after an
 * automatic frame download, makes the frame lists read again: the target
 * context raises its frames reload key, and other events do not.
 */

import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { TargetProvider, useTargetContext } from '../common/context/TargetContext';
import { socketClient } from '../common/utils/socketClient';

/** Shows the frames reload key, which the frame lists use as a refetch trigger. */
const KeyProbe: React.FC = () => {
    const { framesReloadKey } = useTargetContext();
    return <div data-testid="key">{framesReloadKey}</div>;
};

/**
 * Sends an action to every listener the socket client holds.
 * @param action The event name.
 * @param payload The event payload.
 */
function sendAction(action: string, payload: unknown): void {
    (socketClient as unknown as { emit: (name: string, ...args: unknown[]) => void }).emit('action', action, payload);
}

describe('library:frames_updated', () => {
    it('raises the frames reload key, and ignores other events', () => {
        render(
            <TargetProvider>
                <KeyProbe />
            </TargetProvider>,
        );
        expect(screen.getByTestId('key').textContent).toBe('0');

        act(() => sendAction('quality:measured', {}));
        expect(screen.getByTestId('key').textContent).toBe('0');

        act(() => sendAction('library:frames_updated', { targetId: 'M 31', framesAdded: 2 }));
        expect(screen.getByTestId('key').textContent).toBe('1');
    });
});
