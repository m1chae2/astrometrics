/**
 * @fileoverview The one shared question "which observatory is the backend using?" (the real telescope or the
 * simulator). The status header chip, the Observatory source setting and the command rules all read it, so
 * the app asks once, not once per screen.
 */

import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getObservatorySource, ObservatorySourceState } from '../services/observatoryService';
import { socketClient } from '../utils/socketClient';

/** The query key of the backend's observatory source report. */
export const OBSERVATORY_SOURCE_QUERY_KEY = ['observatorySource'] as const;

/** Time between questions while the backend has not answered or is still starting, in milliseconds. */
export const OBSERVATORY_SOURCE_RETRY_MS = 2000;

/**
 * Says whether a report is final, so no more questions are needed.
 *
 * @param state The backend's report.
 * @return True when the backend is not starting and says it is ready. Older backends leave `ready` out.
 */
export function isObservatorySourceSettled(state: ObservatorySourceState | undefined): boolean {
    return !!state && !state.starting && state.ready !== false;
}

/**
 * Reads the backend's observatory source report.
 *
 * It keeps asking until the backend answers (a failed call is tried again) and says it is ready, then stops.
 * A final answer is asked for again only when the event socket comes back after a break, because the
 * backend may have restarted on another source.
 *
 * @param active False to skip the question (for a hidden panel).
 * @return The report, or undefined until the backend answers.
 */
export function useObservatorySourceQuery(active = true): ObservatorySourceState | undefined {
    const queryClient = useQueryClient();
    const { data } = useQuery({
        queryKey: OBSERVATORY_SOURCE_QUERY_KEY,
        queryFn: getObservatorySource,
        enabled: active,
        retry: true,
        retryDelay: OBSERVATORY_SOURCE_RETRY_MS,
        staleTime: Infinity,
        refetchInterval: (query) => (isObservatorySourceSettled(query.state.data) ? false : OBSERVATORY_SOURCE_RETRY_MS),
    });

    const socketWasDown = useRef(false);
    useEffect(() => {
        if (!active) return;
        const onDisconnected = () => { socketWasDown.current = true; };
        const onConnected = () => {
            if (!socketWasDown.current) return;
            socketWasDown.current = false;
            void queryClient.invalidateQueries({ queryKey: OBSERVATORY_SOURCE_QUERY_KEY });
        };
        socketClient.on('disconnected', onDisconnected);
        socketClient.on('connected', onConnected);
        return () => {
            socketClient.off('disconnected', onDisconnected);
            socketClient.off('connected', onConnected);
        };
    }, [active, queryClient]);

    return data;
}
