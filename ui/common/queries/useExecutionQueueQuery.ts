import { useQuery } from '@tanstack/react-query';
import { fetchExecutionQueue } from '../services/sequencerService';

/** Query key for the execution queue, exported so mutations can update the cache directly. */
export const EXECUTION_QUEUE_QUERY_KEY = ['executionQueue'];

/**
 * Shared query for the observation execution queue.
 *
 * @returns {import('@tanstack/react-query').UseQueryResult} The execution queue query result.
 */
export const useExecutionQueueQuery = () =>
    useQuery({ queryKey: EXECUTION_QUEUE_QUERY_KEY, queryFn: fetchExecutionQueue });
