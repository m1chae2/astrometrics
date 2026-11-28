/**
 * @file useTargetReadPrefetch.ts
 * @description Reads the selected target's star shapes and frame statuses ahead of time, so the
 * first visit to Image Processing shows them at once.
 *
 * The Image Processing display asks for these two slow reads only while it is on screen, so they do
 * not slow the start of the app (see `targetReadQueryPolicy.ts`). On its own, that made the first
 * visit slow: the star-shape read can take seconds, and it only started when the display was opened.
 * This hook closes that gap. It waits until start-up is over (`useIsAppReady`) and the browser is
 * idle, then reads the two answers for the selected target into the shared cache. When the display
 * opens, it finds them there and shows them with no new call.
 *
 * Rules:
 * - Only the selected target is read ahead, and only when it has frames on this computer (it is in
 *   the kept target list). Nothing is read during start-up.
 * - A new selection is read ahead once it has stayed the same for a moment
 *   (`SELECTION_SETTLE_MS`), not on every click.
 * - A kept answer that is still fresh, or a read already on its way, is not asked again.
 * - When a change marks the selected target's kept reads as old while the display is hidden,
 *   React Query does not read them again by itself. This hook notices the change and reads them
 *   ahead again at the next idle moment.
 * - The read-ahead uses the same key and read as the display (`starShapesQuery`,
 *   `frameStatusesQuery`), so the display always finds what was read ahead.
 */

import { useEffect, useRef, useState } from 'react';
import { hashKey, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useTargetContext } from '../../common/context/TargetContext';
import { useIsAppReady } from '../../common/utils/appBootReadiness';
import { runWhenIdle } from '../../common/utils/runWhenIdle';
import { TARGET_LIST_QUERY_KEY } from '../../common/queries/useTargetListQuery';
import { isTargetInList, type TargetListEntry } from '../../common/hooks/targetListFiltering';
import { frameStatusesQuery, starShapesQuery } from './targetReadQueryPolicy';

/**
 * How long, in milliseconds, a new selection (or a change to the selected target's data) must stay
 * the same before its reads are asked for. Clicking through the list asks for nothing.
 */
export const SELECTION_SETTLE_MS = 1500;

/** Longest wait, in milliseconds, for the browser to be idle before the read-ahead runs anyway. */
export const PREFETCH_IDLE_TIMEOUT_MS = 2000;

/** Wait, in milliseconds, before the read-ahead runs where the browser has no idle callback. */
export const PREFETCH_FALLBACK_DELAY_MS = 50;

/**
 * Reads one target's star shapes and frame statuses into the cache, unless a kept answer is still
 * fresh or a read is already on its way. A target without frames on this computer is skipped.
 * A failed read is not kept and shows no message; the display asks again when it opens.
 * @param queryClient The app's query client.
 * @param targetId The selected target.
 */
export function prefetchTargetReads(queryClient: QueryClient, targetId: string): void {
    const targetList = queryClient.getQueryData<readonly (TargetListEntry | string)[]>(TARGET_LIST_QUERY_KEY);
    if (!Array.isArray(targetList) || !isTargetInList(targetList, targetId)) return;
    // A read on its way is left alone. `prefetchQuery` itself skips a fresh kept answer.
    const starShapes = starShapesQuery(targetId);
    if (queryClient.getQueryState(starShapes.queryKey)?.fetchStatus !== 'fetching') {
        void queryClient.prefetchQuery(starShapes);
    }
    const frameStatuses = frameStatusesQuery(targetId);
    if (queryClient.getQueryState(frameStatuses.queryKey)?.fetchStatus !== 'fetching') {
        void queryClient.prefetchQuery(frameStatuses);
    }
}

/**
 * Reads the selected target's star shapes and frame statuses ahead, once start-up is over and the
 * browser is idle. Use it once, in the main app.
 */
export function useTargetReadPrefetch(): void {
    const queryClient = useQueryClient();
    const { selectedTarget } = useTargetContext();
    const isAppReady = useIsAppReady();
    // Goes up each time the selected target's kept reads are marked old.
    const [invalidationCount, setInvalidationCount] = useState(0);
    // True once the first read-ahead after start-up has been planned. Later ones wait to settle.
    const hasPlannedFirstRef = useRef(false);

    // Notice when the selected target's kept reads are marked old, so they are read ahead again.
    useEffect(() => {
        if (!selectedTarget) return undefined;
        const watchedHashes = new Set([
            hashKey(starShapesQuery(selectedTarget).queryKey),
            hashKey(frameStatusesQuery(selectedTarget).queryKey),
        ]);
        return queryClient.getQueryCache().subscribe((event) => {
            if (event.type !== 'updated' || event.action.type !== 'invalidate') return;
            if (watchedHashes.has(event.query.queryHash)) setInvalidationCount((count) => count + 1);
        });
    }, [queryClient, selectedTarget]);

    // Plan the read-ahead: right after start-up, then once a new selection or a change has settled.
    useEffect(() => {
        if (!isAppReady || !selectedTarget) return undefined;
        const settleMs = hasPlannedFirstRef.current ? SELECTION_SETTLE_MS : 0;
        hasPlannedFirstRef.current = true;
        let cancelIdleWait: (() => void) | null = null;
        const settleTimer = window.setTimeout(() => {
            cancelIdleWait = runWhenIdle(() => prefetchTargetReads(queryClient, selectedTarget), {
                timeoutMs: PREFETCH_IDLE_TIMEOUT_MS,
                fallbackDelayMs: PREFETCH_FALLBACK_DELAY_MS,
            });
        }, settleMs);
        return () => {
            window.clearTimeout(settleTimer);
            cancelIdleWait?.();
        };
    }, [queryClient, isAppReady, selectedTarget, invalidationCount]);
}

/**
 * Runs {@link useTargetReadPrefetch} and shows nothing. It sits on its own in the app, so a change
 * of target re-renders only this, not the whole app.
 * @return Nothing on screen.
 */
export function TargetReadPrefetcher(): null {
    useTargetReadPrefetch();
    return null;
}
