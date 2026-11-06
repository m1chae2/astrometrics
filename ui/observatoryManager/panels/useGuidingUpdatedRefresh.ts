/**
 * Description: Keeps the past-night guiding views current.
 * When the backend sends `library:guiding_updated` (a guide log download stored new guiding
 * samples), the night list and the night summaries are read again. It shows no message.
 */

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { socketClient } from '../../common/utils/socketClient';
import { UI_EVENTS } from '../../common/types/backendTypes';

/**
 * Reads the given queries again each time new guiding data arrives.
 *
 * @param queryKeys The keys of the queries this view shows.
 */
export function useGuidingUpdatedRefresh(queryKeys: ReadonlyArray<readonly unknown[]>): void {
    const queryClient = useQueryClient();
    // The keys are rebuilt on each render, so the effect depends on their text.
    const keyText = JSON.stringify(queryKeys);
    useEffect(() => {
        const keys = JSON.parse(keyText) as unknown[][];
        const onAction = (action: string): void => {
            if (action !== UI_EVENTS['library:guiding_updated']) return;
            for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey });
        };
        socketClient.on('action', onAction);
        return () => {
            socketClient.off('action', onAction);
        };
    }, [queryClient, keyText]);
}
