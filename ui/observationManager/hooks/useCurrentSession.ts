/**
 * @file useCurrentSession.ts
 * @description Data hook for tonight's observation session with its progress.
 *
 * Backend calls: `plan:current_session` (the session, with the frames captured per queue entry, the
 * entry imaging now and the run state; or null when no session exists yet) and
 * `plan:tick_checklist_item` (ticks one checklist item, naming the revision the window last read).
 * The `plan:changed` and `packages:changed` events refresh the read (handlers in ui/App.tsx). The
 * read also repeats once a minute, because frames arrive without any event.
 */
import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { CURRENT_SESSION_QUERY_KEY } from '../../common/queries/packageLibraryQueryKeys';
import { ObservationSessionWithProgress } from '../../common/types/backendTypes';

/** How often the progress is read again while the display is open, in milliseconds. */
const PROGRESS_REFRESH_MS = 60_000;

/**
 * Loads tonight's session and offers the checklist tick.
 * @return The session (null when none exists), the loading and error state, a retry function,
 *     the tick function and the text of a refused tick.
 */
export const useCurrentSession = () => {
    const queryClient = useQueryClient();
    const query = useQuery({
        queryKey: CURRENT_SESSION_QUERY_KEY,
        queryFn: () => callBackend('plan:current_session', {}, { silent: true }),
        refetchInterval: PROGRESS_REFRESH_MS,
    });
    const [tickError, setTickError] = useState<string | null>(null);

    /** Ticks one checklist item with the revision last read. Resolves to true when it was saved. */
    const tickChecklistItem = useCallback(async (
        session: ObservationSessionWithProgress,
        itemId: string
    ): Promise<boolean> => {
        setTickError(null);
        try {
            await callBackend(
                'plan:tick_checklist_item',
                { session_id: session.id, item_id: itemId, expected_revision: session.revision },
                { silent: true }
            );
            void queryClient.invalidateQueries({ queryKey: CURRENT_SESSION_QUERY_KEY });
            return true;
        } catch (error) {
            setTickError(`Your tick was not saved: ${(error as Error).message}`);
            void queryClient.invalidateQueries({ queryKey: CURRENT_SESSION_QUERY_KEY });
            return false;
        }
    }, [queryClient]);

    return {
        session: query.data ?? null,
        isLoading: query.isLoading,
        error: query.error ? (query.error as Error).message || 'Unknown error' : null,
        retry: () => { void query.refetch(); },
        tickChecklistItem,
        tickError,
        dismissTickError: () => setTickError(null),
    };
};
