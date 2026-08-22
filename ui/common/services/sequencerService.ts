/**
 * @fileoverview Service for managing telescope sequence planning and execution queues.
 * Interacts with backend JSON-RPC methods.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';

/**
 * Fetches the current execution queue from the backend.
 * @return Mapped array of planned and queued sequences.
 */
export async function fetchExecutionQueue(): Promise<any[]> {
    try {
        const data = await callBackend("sequencer:get_queue", {});
        return Array.isArray(data) ? data : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
    }
}

/**
 * Creates an imaging plan for a target.
 * @param targetName The target name.
 * @param items List of plan items (exposures, count, filter).
 * @return Mapped imaging sequence object.
 */
export async function createImagingPlan(targetName: string, items: any[]): Promise<any> {
    try {
        return await callBackend("sequencer:create_plan", { target_name: targetName, items });
    } catch (err: unknown) {
        console.error('Failed to create imaging plan', err);
        throw err;
    }
}

/**
 * Adds an imaging sequence to the queue.
 * @param sequence The imaging sequence dictionary.
 * @param timing Timing options.
 */
export async function addToQueue(
    sequence: any,
    timing: { mode: string; time?: string } = { mode: 'soonest' }
): Promise<void> {
    try {
        await callBackend("sequencer:add", { sequence, timing });
    } catch (err: unknown) {
        console.error('Failed to add to queue', err);
        throw err;
    }
}

/**
 * Removes an item from the queue by ID.
 * @param sequenceId The sequence ID.
 */
export async function removeFromQueue(sequenceId: string): Promise<void> {
    try {
        const success = await callBackend("sequencer:remove", { sequence_id: sequenceId });
        if (!success) {
            throw new Error(`Failed to remove queue item: ${sequenceId}`);
        }
    } catch (err: unknown) {
        console.error('Failed to remove from queue', err);
        throw err;
    }
}

/**
 * Reorders the queue elements based on an ordered array of IDs.
 * @param sequenceIds The ordered list of sequence IDs.
 */
export async function reorderQueue(sequenceIds: string[]): Promise<void> {
    try {
        const success = await callBackend("sequencer:reorder", { sequence_ids: sequenceIds });
        if (!success) {
            throw new Error('Failed to reorder queue');
        }
    } catch (err: unknown) {
        console.error('Failed to reorder queue', err);
        throw err;
    }
}

/**
 * Begins execution of the active sequencing queue.
 */
export async function beginImagingSession(): Promise<void> {
    try {
        await callBackend("sequencer:begin", {});
    } catch (err: unknown) {
        console.error('Failed to begin imaging session', err);
        throw err;
    }
}

/**
 * Modifies an existing queue item's parameters.
 * @param sequenceId The sequence ID.
 * @param sequence Updated sequence properties.
 */
export async function modifyQueueItem(sequenceId: string, sequence: any): Promise<void> {
    try {
        const success = await callBackend("sequencer:modify", { sequence_id: sequenceId, sequence });
        if (!success) {
            throw new Error(`Failed to modify queue item: ${sequenceId}`);
        }
    } catch (err: unknown) {
        console.error('Failed to modify queue item', err);
        throw err;
    }
}
