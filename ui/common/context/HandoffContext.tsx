/**
 * @fileoverview The workspace state shared by every app window (the "handoff" state).
 *
 * The backend keeps one copy of the current target and the planning time. This
 * provider reads that copy when the window starts (`handoff:get_state`), keeps
 * it current from the `handoff` socket event, and sends this window's changes
 * (`handoff:update_state`). `TargetContext` and `PlanningTimeContext` build on
 * it. The socket is a prop so a test can run two windows with two fake sockets.
 */

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { callBackend, HandoffState } from '../services/backendApi';
import { socketClient } from '../utils/socketClient';

/** The part of a socket this provider uses: it can add and remove an action listener. */
export interface HandoffSocket {
    on: (event: 'action', listener: (action: string, payload: any) => void) => unknown;
    off: (event: 'action', listener: (action: string, payload: any) => void) => unknown;
}

/** The fields a window may change in the shared state. */
export interface HandoffUpdate {
    selected_target?: string;
    planning_time?: string;
    clear_planning_time?: boolean;
}

/** What `useOptionalHandoff` returns. */
export interface HandoffContextValue {
    /** The shared state, or `null` until the backend has answered. */
    state: HandoffState | null;
    /** Sends a change to the backend. The reply becomes the new state. */
    publish: (update: HandoffUpdate) => Promise<void>;
}

const HandoffContext = createContext<HandoffContextValue | undefined>(undefined);

interface HandoffProviderProps {
    children: ReactNode;
    /** The socket that carries `handoff` events. Defaults to the app's socket. */
    socket?: HandoffSocket;
}

/**
 * Keeps this window's copy of the shared workspace state.
 * @param props The children and, for tests, a replacement socket.
 * @return The children inside the handoff context.
 */
export const HandoffProvider: React.FC<HandoffProviderProps> = ({ children, socket = socketClient }) => {
    const [state, setState] = useState<HandoffState | null>(null);

    /** Keeps the newer of the current and the incoming state, so a late reply cannot undo a newer one. */
    const adopt = useCallback((incoming: HandoffState | undefined | null) => {
        if (!incoming) return;
        setState((current) => (current && current.updated_at > incoming.updated_at ? current : incoming));
    }, []);

    useEffect(() => {
        let cancelled = false;
        callBackend('handoff:get_state', {}, { silent: true })
            .then((reply) => {
                if (!cancelled) adopt(reply);
            })
            .catch(() => {});
        const onAction = (action: string, payload: any) => {
            if (action === 'handoff') adopt(payload as HandoffState);
        };
        socket.on('action', onAction);
        return () => {
            cancelled = true;
            socket.off('action', onAction);
        };
    }, [socket, adopt]);

    const publish = useCallback(
        async (update: HandoffUpdate) => {
            try {
                const reply = await callBackend(
                    'handoff:update_state',
                    { ...update, origin_device: 'desktop' },
                    { silent: true },
                );
                adopt(reply);
            } catch {
                // The next handoff event or restart brings the window back in line.
            }
        },
        [adopt],
    );

    const value = useMemo(() => ({ state, publish }), [state, publish]);
    return <HandoffContext.Provider value={value}>{children}</HandoffContext.Provider>;
};

/**
 * Reads the handoff state without requiring a provider.
 * @return The handoff context, or `undefined` outside a `HandoffProvider`
 *     (a window that is not synchronized, such as an isolated test).
 */
export const useOptionalHandoff = (): HandoffContextValue | undefined => useContext(HandoffContext);
