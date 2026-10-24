/**
 * @fileoverview React Query hooks for the three advisory reads.
 *
 * `useTargetReport` reads the quality report of one target, `useTonightRanking`
 * reads what to image at a chosen time, and `useResultStatements` reads the
 * statements about one science result. The hooks only fetch; they do no
 * ranking or sorting.
 *
 * The target report and result keys start with the shared quality prefix and
 * carry an object with `target_id`, so `isAffectedByMeasurement` refreshes
 * them when a measurement job stores new rows. The tonight ranking is costly,
 * so its key does NOT start with the quality prefix: it refetches when its
 * time changes or when the user presses "Refresh".
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../services/backendApi';
import { qualityQueryKeys } from './qualityQueryKeys';

/**
 * Reads the quality report of one target.
 * @param targetId The target's id. With no id the hook does not fetch.
 * @return The query result with the `TargetDatasetReport`.
 */
export const useTargetReport = (targetId: string | null | undefined) =>
    useQuery({
        queryKey: qualityQueryKeys.targetReport(targetId ?? ''),
        queryFn: () => callBackend('advisory:target_report', { target: targetId as string }),
        enabled: Boolean(targetId),
    });

/**
 * Reads the tonight ranking.
 * @param planningTime The planning time as ISO 8601 text with a UTC offset, or null for Now.
 * @return The query result with the `TonightRanking`.
 */
export const useTonightRanking = (planningTime: string | null = null) =>
    useQuery({
        queryKey: qualityQueryKeys.tonightRanking(planningTime),
        queryFn: () => callBackend('advisory:tonight', planningTime ? { at: planningTime } : {}),
    });

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
