/**
 * @fileoverview React Query hook for the advisory read of one science result.
 *
 * `useResultStatements` reads the statements about one science result. The hook
 * only fetches; it does no ranking or sorting.
 *
 * The result key starts with the shared quality prefix and carries an object
 * with `target_id`, so `isAffectedByMeasurement` refreshes it when a
 * measurement job stores new rows.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../services/backendApi';
import { qualityQueryKeys } from './qualityQueryKeys';

/**
 * Reads the statements about one science result.
 * @param targetId The target's id.
 * @param objectId The id of the result's object. With no id the hook does not fetch.
 * @return The query result with the list of `TargetStatement`.
 */
export const useResultStatements = (targetId: string | null | undefined, objectId: string | null | undefined) =>
    useQuery({
        queryKey: qualityQueryKeys.resultStatements(targetId ?? '', objectId ?? ''),
        queryFn: () => callBackend('advisory:result', { target: targetId as string, object_id: objectId as string }),
        enabled: Boolean(targetId) && Boolean(objectId),
    });
