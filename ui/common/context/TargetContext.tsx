/**
 * @file TargetContext.tsx
 * @description Context provider and hooks for target selection and metadata sharing across application displays.
 * Synchronizes selected targets with local storage and global application events.
 */

import React, { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode } from 'react';
import { on as onEvent } from '../utils/eventBus';
import { observeHandoffs } from '../utils/displayHandoffStore';
import { socketClient } from '../utils/socketClient';
import { UI_EVENTS } from '../types/backendTypes';
import { DisplayActiveContext } from './DisplayActiveContext';
import { QueryClientContext } from '@tanstack/react-query';
import { invalidateTargetReads } from '../queries/targetReadQueryKeys';
import { useOptionalHandoff } from './HandoffContext';

export type InvalidationScope = 'targets' | 'frames' | 'all';

/** The payload of the `targetsUpdated` app event. `id` names the target that changed, when one did. */
interface TargetsUpdatedPayload {
    id?: string;
}

/** The payload of the backend's `library:frames_updated` event. */
interface FramesUpdatedPayload {
    targetId?: string;
    framesAdded?: number;
    /** True when the sync was of a calibration folder (bias, dark or flat), not a target. */
    isCalibration?: boolean;
}

/**
 * Interface defining the shape of the TargetContext state.
 */
export interface TargetContextValue {
    selectedTarget: string;
    setSelectedTarget: (id: string) => void;
    pendingTarget: string;
    setPendingTarget: (id: string) => void;
    reloadKey: number;
    framesReloadKey: number;
    forceReload: () => void;
    /**
     * Tells the screens that library data changed, so they read it again.
     * @param scope Which reload count goes up: target data, frame lists, or both.
     * @param targetIds The targets that changed. Only their kept reads (star shapes, frame statuses,
     *     grouped frames) are marked old. Leave it out when the change names no target: then every
     *     target's kept reads are marked old.
     */
    invalidate: (scope?: InvalidationScope, targetIds?: readonly string[]) => void;
    // Shared editing state
    catalogId: string;
    setCatalogId: (val: string) => void;
    commonName: string;
    setCommonName: (val: string) => void;
}

const TargetContext = createContext<TargetContextValue | undefined>(undefined);

interface TargetProviderProps {
    children: ReactNode;
}

/**
 * Top-level provider for active target selection state and metadata synchronization.
 */
export const TargetProvider: React.FC<TargetProviderProps> = ({ children }) => {
    const [selectedTarget, setSelectedTargetState] = useState<string>(() => {
        try {
            return window.localStorage.getItem('selectedTarget') || '';
        } catch {
            return '';
        }
    });
    const [pendingTarget, setPendingTargetState] = useState<string>(() => {
        try {
            return window.localStorage.getItem('selectedTarget') || '';
        } catch {
            return '';
        }
    });
    const [reloadKey, setReloadKey] = useState<number>(0);
    const [framesReloadKey, setFramesReloadKey] = useState<number>(0);
    // Read without `useQueryClient`, so the provider still works where there is no query client.
    const queryClient = useContext(QueryClientContext);

    const handoff = useOptionalHandoff();
    const publishHandoff = handoff?.publish;
    // The target this window holds now. Read inside callbacks so a target
    // received from another window is never published back (no echo).
    const selectedTargetRef = useRef<string>(selectedTarget);

    /** Holds a target in state and local storage without telling the backend. */
    const holdTarget = useCallback((id: string) => {
        selectedTargetRef.current = id;
        setSelectedTargetState(id);
        try {
            if (id) {
                window.localStorage.setItem('selectedTarget', id);
            }
        } catch {
            // Ignore
        }
    }, []);

    /** The user chooses a target: hold it and publish it when it is a change. */
    const setSelectedTarget = useCallback(
        (id: string) => {
            const changed = id !== selectedTargetRef.current;
            holdTarget(id);
            if (changed && id && publishHandoff) void publishHandoff({ selected_target: id });
        },
        [holdTarget, publishHandoff],
    );

    // Adopt the backend's target at start and whenever another window changes it.
    const sharedTarget = handoff?.state?.selected_target ?? null;
    useEffect(() => {
        if (!sharedTarget || sharedTarget === selectedTargetRef.current) return;
        holdTarget(sharedTarget);
        setPendingTargetState(sharedTarget);
    }, [sharedTarget, holdTarget]);

    const setPendingTarget = useCallback((id: string) => {
        setPendingTargetState(id);
    }, []);

    // Shared editing state
    const [catalogId, setCatalogId] = useState<string>('');
    const [commonName, setCommonName] = useState<string>('');

    const invalidate = useCallback((scope: InvalidationScope = 'all', targetIds?: readonly string[]) => {
        if (scope === 'targets' || scope === 'all') {
            setReloadKey((prev) => prev + 1);
        }
        if (scope === 'frames' || scope === 'all') {
            setFramesReloadKey((prev) => prev + 1);
        }
        // The kept target reads do not use the reload counts. Only the named targets' reads are
        // marked old, so the other targets keep theirs.
        if (queryClient) void invalidateTargetReads(queryClient, targetIds);
    }, [queryClient]);

    const forceReload = useCallback(() => {
        invalidate('targets');
    }, [invalidate]);

    // Synchronize external target selection events (e.g. from Astronomy Manager, Planetarium, or WebSocket)
    useEffect(() => {
        // Watch every "target selected" hand-off, whichever display it is for.
        // This provider is always running, so it needs no waiting copy.
        return observeHandoffs('targetSelected', (handoff) => {
            const raw = handoff.payload as string | { targetId?: string } | null | undefined;
            const targetId = typeof raw === 'string' ? raw : raw?.targetId;
            if (targetId) {
                setSelectedTarget(targetId);
                setPendingTarget(targetId);
            }
        });
    }, [setSelectedTarget, setPendingTarget]);

    useEffect(() => {
        const detach = onEvent('targetsUpdated', (payload) => {
            const targetId = (payload as TargetsUpdatedPayload | undefined)?.id;
            invalidate('targets', targetId ? [targetId] : undefined);
        });
        return () => {
            try {
                detach();
            } catch {
                // Ignore
            }
        };
    }, [invalidate]);

    // A download added frames to the library: read the frame lists again. Only the synced
    // target's kept reads are marked old. A calibration folder sync changes every target's
    // calibration matches, so it marks them all old.
    useEffect(() => {
        const onAction = (action: string, payload?: unknown): void => {
            if (action !== UI_EVENTS['library:frames_updated']) return;
            const framesUpdated = (payload ?? {}) as FramesUpdatedPayload;
            const isOneTarget = Boolean(framesUpdated.targetId) && !framesUpdated.isCalibration;
            invalidate('frames', isOneTarget ? [framesUpdated.targetId as string] : undefined);
        };
        socketClient.on('action', onAction);
        return () => {
            socketClient.off('action', onAction);
        };
    }, [invalidate]);

    const value: TargetContextValue = {
        selectedTarget,
        setSelectedTarget,
        pendingTarget,
        setPendingTarget,
        reloadKey,
        framesReloadKey,
        forceReload,
        invalidate,
        catalogId,
        setCatalogId,
        commonName,
        setCommonName
    };

    return (
        <TargetContext.Provider value={value}>
            {children}
        </TargetContext.Provider>
    );
};

interface DeferWhileHiddenProps {
    /** Whether the display wrapped by this component is the one on screen. */
    active: boolean;
    children: ReactNode;
}

/**
 * Keeps a hidden display from reacting to target changes.
 *
 * Every visited display stays mounted and is only hidden with CSS, so each
 * click on a target used to make all of them fetch that target's data, even
 * the ones nobody could see (about 20 network calls per click). While
 * `active` is false this passes down the last target state the display saw
 * while it was on screen; when the display is shown again it gets the
 * current state and loads it then.
 *
 * @param props Whether the display is active, and the display itself.
 * It also tells the display whether it is active (see `DisplayActiveContext`) so
 * its pollers can pause while hidden.
 *
 * @return The children, inside a target context that holds still while hidden.
 */
export const DeferWhileHidden: React.FC<DeferWhileHiddenProps> = ({ active, children }) => {
    const live = useContext(TargetContext);
    const [lastSeen, setLastSeen] = useState(live);
    useEffect(() => {
        if (active) setLastSeen(live);
    }, [active, live]);
    // While active the live value is used directly; the saved copy is only read once hidden.
    const value = active ? live : lastSeen;
    return (
        <DisplayActiveContext.Provider value={active}>
            <TargetContext.Provider value={value}>{children}</TargetContext.Provider>
        </DisplayActiveContext.Provider>
    );
};

/**
 * Hook to consume the TargetContext.
 * @throws Error if used outside of a TargetProvider.
 */
export const useTargetContext = (): TargetContextValue => {
    const context = useContext(TargetContext);
    if (context === undefined) {
        throw new Error('useTargetContext must be used within a TargetProvider');
    }
    return context;
};

/**
 * Optional hook to consume TargetContext without throwing when unmounted in isolated tests.
 * @returns TargetContextValue or undefined if outside a provider.
 */
export const useOptionalTargetContext = (): TargetContextValue | undefined => {
    return useContext(TargetContext);
};
