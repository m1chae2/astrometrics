/**
 * @file test_planetariumToolbarSessions.test.tsx
 * @description Tests that the Planetarium toolbar still offers its session picker (now the shared
 * SessionPicker): "No Session Selected" first, "All Sessions (Cumulative)" and each dated
 * session with its counts, the picked entry on the button, and the pick reported to the caller.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '@testing-library/react';

vi.mock('../../common/components/PlanningTimeIndicator', () => ({ PlanningTimeIndicator: () => null }));

import { PlanetariumToolbar } from '../components/PlanetariumToolbar';

const sessions = [
    { sessionId: '2026-09-24', sessionDate: '2026-09-24', syncCount: 1, targetCount: 8, polarErrorArcsec: 120 },
    { sessionId: '2026-09-23', sessionDate: '2026-09-23', syncCount: 2, targetCount: null },
];

/**
 * Renders the toolbar with the given session choice.
 * @param selectedSessionId The picked session id, 'all', or null.
 * @param onSelectSession The pick handler.
 * @return The render result.
 */
function renderToolbar(selectedSessionId: string | null, onSelectSession = vi.fn()) {
    const noop = (): void => undefined;
    return render(
        <PlanetariumToolbar
            showStars onToggleStars={noop} showFOV onToggleFOV={noop} showEnvironment onToggleEnvironment={noop}
            showGrid onToggleGrid={noop} showCatalog onToggleCatalog={noop} showConstellations onToggleConstellations={noop}
            showTelescope onToggleTelescope={noop} availableSessions={sessions}
            selectedSessionId={selectedSessionId} onSelectSession={onSelectSession}
            currentFOV={10} onOpenTimeModal={noop}
        />,
    );
}

describe('PlanetariumToolbar session picker', () => {
    /** With nothing picked the button reads "No Session Selected" and the menu lists the choices. */
    it('offers no session, all sessions and each dated session', async () => {
        const user = userEvent.setup();
        renderToolbar(null);
        await user.click(screen.getByRole('button', { name: 'Session: No Session Selected' }));
        const menu = screen.getByRole('group', { name: 'Session' });
        expect(within(menu).getAllByRole('button').map((entry) => entry.textContent)).toEqual([
            'No Session Selected',
            'All Sessions (Cumulative)',
            "2026-09-24 (8 targets, 1 sync • PA: 2.0')",
            '2026-09-23 (2 syncs)',
        ]);
    });

    /** Picking an entry reports its id; picking the first entry reports null. */
    it('reports the picked session', async () => {
        const user = userEvent.setup();
        const onSelectSession = vi.fn();
        renderToolbar('all', onSelectSession);
        expect(screen.getByRole('button', { name: 'Session: All Sessions (Cumulative)' })).toBeTruthy();
        await user.click(screen.getByRole('button', { name: /^Session:/ }));
        await user.click(screen.getByRole('button', { name: '2026-09-23 (2 syncs)' }));
        expect(onSelectSession).toHaveBeenLastCalledWith('2026-09-23');
        await user.click(screen.getByRole('button', { name: /^Session:/ }));
        await user.click(screen.getByRole('button', { name: 'No Session Selected' }));
        expect(onSelectSession).toHaveBeenLastCalledWith(null);
    });
});
