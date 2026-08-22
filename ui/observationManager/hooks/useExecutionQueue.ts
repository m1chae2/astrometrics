// Types are now inferred or can be imported if shared.
import { useState, useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    createImagingPlan,
    addToQueue,
    removeFromQueue,
    reorderQueue,
    beginImagingSession,
    modifyQueueItem
} from '../../common/services/sequencerService';
import { useExecutionQueueQuery, EXECUTION_QUEUE_QUERY_KEY } from '../../common/queries/useExecutionQueueQuery';

export interface SequenceObject {
    id: string;
    target_name: string;
    items: any[];
    total_duration: number;
    timing?: {
        mode: 'soonest' | 'at';
        time?: string;
    };
    status: string;
}

/**
 * Hook to manage the execution queue, interacting with the backend.
 */
export const useExecutionQueue = () => {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const queryClient = useQueryClient();
    const executionQueueQuery = useExecutionQueueQuery();
    const queue = useMemo(
        () => (executionQueueQuery.data as SequenceObject[]) ?? [],
        [executionQueueQuery.data]
    );

    const refreshQueue = useCallback(async () => {
        try {
            await executionQueueQuery.refetch();
        } catch (err: any) {
            setError(err.message);
        }
    }, [executionQueueQuery]);

    const add = useCallback(async (targetName: string, items: any[]) => {
        setLoading(true);
        try {
            const sequence = await createImagingPlan(targetName, items);
            await addToQueue(sequence);
            await refreshQueue();
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [refreshQueue]);

    const remove = useCallback(async (id: string) => {
        try {
            await removeFromQueue(id);
            await refreshQueue();
        } catch (err: any) {
            setError(err.message);
        }
    }, [refreshQueue]);

    const beginImaging = useCallback(async () => {
        try {
            await beginImagingSession();
            // Toast success could be added here if needed
        } catch (err: any) {
            setError(err.message);
        }
    }, []);

    const reorder = useCallback(async (newQueue: SequenceObject[]) => {
        const originalQueue = queue;
        queryClient.setQueryData(EXECUTION_QUEUE_QUERY_KEY, newQueue);

        try {
            const ids = newQueue.map(item => item.id);
            await reorderQueue(ids);
        } catch (err: any) {
            setError(err.message);
            queryClient.setQueryData(EXECUTION_QUEUE_QUERY_KEY, originalQueue);
        }
    }, [queue, queryClient]);

    const updateQueueItem = useCallback(async (id: string, targetName: string, items: any[]) => {
        setLoading(true);
        try {
            // Create plan then modify
            const sequence = await createImagingPlan(targetName, items);
            await modifyQueueItem(id, sequence);
            await refreshQueue();
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [refreshQueue]);

    const clearError = useCallback(() => setError(null), []);

    return {
        queue,
        loading,
        error,
        clearError,
        addToQueue: add,
        removeFromQueue: remove,
        updateQueueItem,
        beginImaging,
        refreshQueue,
        reorderQueue: reorder,
    };
};
