/**
 * @fileoverview The keys of the slow reads the app keeps for each target, and the one way to mark them old.
 *
 * Three reads are kept for each target: its star-shape verdicts, its frame statuses and its light
 * frames grouped by gain, filter and exposure. Their keys name the target and nothing else, so a
 * change to one target does not throw away the kept reads of the others.
 *
 * When something changes a target's data (new frames, a restack, an edit), call
 * `invalidateTargetReads` with that target's id. Only its kept reads are marked old. When a change
 * names no target, or changes the whole library (such as new calibration frames), call it with no
 * ids, and every target's kept reads are marked old. A read on screen is read again at once; a read
 * not on screen is read again the next time it is shown.
 */

import type { QueryClient } from '@tanstack/react-query';
import { QUALITY_QUERY_KEY_PREFIX, qualityQueryKeys } from './qualityQueryKeys';

/** The start of every grouped light-frame key. */
export const GROUPED_FRAMES_QUERY_KEY_PREFIX = ['targetFramesGrouped'] as const;

/**
 * The key of a `target:get_frames_grouped` call.
 * @param targetId The target whose light frames are summed up.
 * @return The key, with `target_id` as its second item.
 */
export const groupedFramesQueryKey = (targetId: string) =>
    [...GROUPED_FRAMES_QUERY_KEY_PREFIX, { target_id: targetId }] as const;

/** The second item of the quality keys that are kept target reads: star shapes and frame statuses. */
const QUALITY_TARGET_READ_NAMES: ReadonlySet<unknown> = new Set([
    qualityQueryKeys.starShapes('')[1],
    qualityQueryKeys.frameStatuses('')[1],
]);

/**
 * Finds the target a kept target read is about.
 * @param queryKey The React Query key of a cached query.
 * @return The target id, or `null` when the key is not one of the three kept target reads.
 */
export function targetOfTargetRead(queryKey: readonly unknown[]): string | null {
    let params: unknown = null;
    if (queryKey[0] === GROUPED_FRAMES_QUERY_KEY_PREFIX[0]) {
        params = queryKey[1];
    } else if (queryKey[0] === QUALITY_QUERY_KEY_PREFIX[0] && QUALITY_TARGET_READ_NAMES.has(queryKey[1])) {
        params = queryKey[2];
    }
    if (!params || typeof params !== 'object') return null;
    const targetId = (params as Record<string, unknown>)['target_id'];
    return typeof targetId === 'string' ? targetId : null;
}

/**
 * Marks kept target reads as old, so they are read again.
 * @param queryClient The app's query client.
 * @param targetIds The targets that changed. Leave it out, or give no usable id, when the change
 *     names no target: then the reads of every target are marked old.
 * @return A promise that settles when the reads on screen are read again.
 */
export function invalidateTargetReads(
    queryClient: QueryClient,
    targetIds?: readonly (string | null | undefined)[] | null,
): Promise<void> {
    const namedTargets = new Set((targetIds ?? []).filter((id): id is string => typeof id === 'string' && id !== ''));
    return queryClient.invalidateQueries({
        predicate: (query) => {
            const targetId = targetOfTargetRead(query.queryKey);
            if (targetId === null) return false;
            return namedTargets.size === 0 || namedTargets.has(targetId);
        },
    });
}
