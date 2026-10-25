/**
 * @fileoverview Tests that two app windows share the current target and the
 * planning time through the backend's handoff state. Each window is its own
 * set of providers with its own fake socket; a fake backend keeps the one
 * shared state and sends the `handoff` event to both sockets.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { HandoffProvider, HandoffSocket } from '../common/context/HandoffContext';
import { TargetProvider, useTargetContext } from '../common/context/TargetContext';
import { PlanningTimeProvider, usePlanningTime, toIsoWithLocalOffset } from '../common/context/PlanningTimeContext';
import { PlanningTimeIndicator } from '../common/components/PlanningTimeIndicator';
import { CurrentTargetAnnouncer } from '../common/components/CurrentTargetAnnouncer';
import { handlePlanChanged } from '../common/utils/planChanged';
import { PLAN_QUERY_KEY_PREFIX } from '../common/queries/qualityQueryKeys';

type Listener = (action: string, payload: any) => void;

/** A fake socket that a test can send events through. */
class FakeSocket implements HandoffSocket {
    listeners = new Set<Listener>();
    on(_event: 'action', listener: Listener) {
        this.listeners.add(listener);
    }
    off(_event: 'action', listener: Listener) {
        this.listeners.delete(listener);
    }
    emit(action: string, payload: any) {
        this.listeners.forEach((listener) => listener(action, payload));
    }
}

const backend = vi.hoisted(() => ({
    state: { active_mode: 'Image Viewer', selected_target: null as string | null, planning_time: null as string | null, coordinates: {}, telemetry: {}, origin_device: 'desktop', updated_at: 1 },
    sockets: [] as Array<{ emit: (action: string, payload: any) => void }>,
    calls: [] as Array<[string, any]>,
}));

vi.mock('../common/services/backendApi', () => ({
    callBackend: vi.fn(async (method: string, params: any) => {
        backend.calls.push([method, params]);
        if (method === 'handoff:update_state') {
            if (params.selected_target !== undefined) backend.state.selected_target = params.selected_target;
            if (params.clear_planning_time) backend.state.planning_time = null;
            else if (params.planning_time !== undefined) backend.state.planning_time = params.planning_time;
            backend.state = { ...backend.state, updated_at: backend.state.updated_at + 1 };
            const snapshot = { ...backend.state };
            backend.sockets.forEach((socket) => socket.emit('handoff', snapshot));
            return snapshot;
        }
        return { ...backend.state };
    }),
}));
vi.mock('../common/utils/socketClient', () => ({ socketClient: { on: vi.fn(), off: vi.fn() } }));

/** One window: its providers, a probe showing target and time, and buttons. */
function Window({ label, socket }: { label: string; socket: FakeSocket }): React.ReactElement {
    return (
        <HandoffProvider socket={socket}>
            <TargetProvider>
                <PlanningTimeProvider>
                    <section data-testid={label}>
                        <Probe label={label} />
                        <PlanningTimeIndicator />
                        <CurrentTargetAnnouncer />
                    </section>
                </PlanningTimeProvider>
            </TargetProvider>
        </HandoffProvider>
    );
}

/** Shows the target and gives buttons to change it and the time. */
function Probe({ label }: { label: string }): React.ReactElement {
    const { selectedTarget, setSelectedTarget } = useTargetContext();
    const { planningTime, setPlanningTime } = usePlanningTime();
    return (
        <div>
            <span data-testid={`${label}-target`}>{selectedTarget}</span>
            <span data-testid={`${label}-time`}>{planningTime ?? 'null'}</span>
            <button onClick={() => setSelectedTarget('M 81')}>{`${label} pick`}</button>
            {/* Today at 22:00, so the indicator shows the clock time without a date on any day. */}
            <button
                onClick={() => {
                    const tonight = new Date();
                    tonight.setHours(22, 0, 0, 0);
                    setPlanningTime(tonight);
                }}
            >{`${label} fix`}</button>
        </div>
    );
}

/** Renders two windows side by side with separate fake sockets. */
function renderTwoWindows() {
    const socketA = new FakeSocket();
    const socketB = new FakeSocket();
    backend.sockets = [socketA, socketB];
    render(
        <>
            <Window label="A" socket={socketA} />
            <Window label="B" socket={socketB} />
        </>,
    );
    return { socketA, socketB };
}

describe('shared target and planning time', () => {
    beforeEach(() => {
        window.localStorage.clear();
        backend.state = { active_mode: 'Image Viewer', selected_target: null, planning_time: null, coordinates: {}, telemetry: {}, origin_device: 'desktop', updated_at: 1 };
        backend.calls = [];
    });

    it('publishes a chosen target and the other window follows without publishing back', async () => {
        renderTwoWindows();
        await userEvent.click(screen.getByText('A pick'));
        await waitFor(() => expect(screen.getByTestId('B-target').textContent).toBe('M 81'));
        const publishes = backend.calls.filter(([method]) => method === 'handoff:update_state');
        expect(publishes).toHaveLength(1);
        expect(publishes[0][1].selected_target).toBe('M 81');
    });

    it('adopts the backend state at start and does not publish the stored target', async () => {
        window.localStorage.setItem('selectedTarget', 'M 13');
        backend.state.selected_target = 'NGC 7331';
        backend.state.planning_time = '2026-10-24T22:00:00-06:00';
        renderTwoWindows();
        await waitFor(() => expect(screen.getByTestId('A-target').textContent).toBe('NGC 7331'));
        expect(screen.getByTestId('B-time').textContent).toBe('2026-10-24T22:00:00-06:00');
        expect(backend.calls.filter(([method]) => method === 'handoff:update_state')).toHaveLength(0);
    });

    it('shows Now, then a fixed time in both windows, then Back to Now clears it', async () => {
        renderTwoWindows();
        await waitFor(() => expect(screen.getByTestId('A').textContent).toContain('Planning time: Now'));
        await userEvent.click(screen.getByText('A fix'));
        await waitFor(() => expect(screen.getByTestId('B').textContent).toContain('fixed at 22:00'));
        const sent = backend.calls.find(([method]) => method === 'handoff:update_state')![1];
        expect(sent.planning_time).toMatch(/T22:00:00[+-]\d\d:\d\d$/);

        const backButtons = screen.getAllByText('Back to Now');
        await userEvent.click(backButtons[0]);
        await waitFor(() => expect(screen.getByTestId('B').textContent).toContain('Planning time: Now'));
        expect(backend.calls[backend.calls.length - 1][1].clear_planning_time).toBe(true);
    });

    it('announces the current target when it changes, not at start', async () => {
        renderTwoWindows();
        expect(screen.getAllByTestId('current-target-announcer')[0].textContent).toBe('');
        await userEvent.click(screen.getByText('A pick'));
        await waitFor(() => expect(screen.getAllByTestId('current-target-announcer')[1].textContent).toBe('Current target: M 81'));
    });
});

describe('toIsoWithLocalOffset', () => {
    it('writes text with a UTC offset', () => {
        expect(toIsoWithLocalOffset(new Date(2026, 9, 24, 22, 5, 9))).toMatch(/^2026-10-24T22:05:09[+-]\d\d:\d\d$/);
    });
});

describe('handlePlanChanged', () => {
    it('invalidates plan queries and tells a stale cache from a current one', () => {
        const client = new QueryClient();
        client.setQueryData([...PLAN_QUERY_KEY_PREFIX, 'session', { session_id: 'night-1' }], { id: 'night-1', revision: 3 });
        client.setQueryData(['other'], 1);
        expect(handlePlanChanged(client, { sessionId: 'night-1', revision: 4 })).toBe(true);
        const states = client.getQueryCache().getAll().map((query) => [query.queryKey[0], query.state.isInvalidated]);
        expect(states).toContainEqual(['plan', true]);
        expect(states).toContainEqual(['other', false]);
        expect(handlePlanChanged(client, { sessionId: 'night-1', revision: 3 })).toBe(false);
    });
});
