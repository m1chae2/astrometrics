/**
 * Description: The alignment attempts of one past night.
 * The Planetarium lists alignment sessions with `telescope:list_alignment_sessions`; each is one
 * observing night. This hook finds the session of a night (its id or its date matches the night)
 * and reads that session's attempts with `telescope:get_session_alignment`. A night with no
 * session has no alignment attempts, and the hook says so without asking for any.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { AlignmentAttempt } from '../../common/types/backendTypes';

/** What the hook returns. */
export interface NightAlignmentAttempts {
    /** The night's attempts, oldest first. Empty while loading, on error, or when the night has none. */
    attempts: AlignmentAttempt[];
    /** True while the session list or the attempts are loading. */
    isLoading: boolean;
    /** True when a read failed. */
    isError: boolean;
}

/**
 * Reads the alignment attempts of one night.
 *
 * @param nightId The night, such as "2026-11-04", or null to read nothing (Live).
 * @returns The attempts and the loading and error flags.
 */
export function useNightAlignmentAttempts(nightId: string | null): NightAlignmentAttempts {
    const sessionsQuery = useQuery({
        queryKey: ['observatory', 'alignmentSessions'],
        queryFn: () => callBackend('telescope:list_alignment_sessions', {}),
        enabled: nightId !== null,
        retry: false,
    });
    const session = (sessionsQuery.data ?? []).find(
        (entry) => entry.sessionId === nightId || entry.sessionDate === nightId,
    );
    const attemptsQuery = useQuery({
        queryKey: ['observatory', 'alignmentSessionAttempts', session?.sessionId ?? null],
        queryFn: () => callBackend('telescope:get_session_alignment', { session_id: session?.sessionId }),
        enabled: nightId !== null && session !== undefined,
        retry: false,
    });
    if (nightId === null) return { attempts: [], isLoading: false, isError: false };
    return {
        attempts: attemptsQuery.data?.alignmentAttempts ?? [],
        isLoading: sessionsQuery.isLoading || (session !== undefined && attemptsQuery.isLoading),
        isError: sessionsQuery.isError || attemptsQuery.isError,
    };
}
