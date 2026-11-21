/**
 * @file frameReviewService.ts
 * @description Calls the backend for what the File Browser shows about a target's frames: each frame's
 * status (included, not stacked yet, set aside), and the action that puts a set-aside frame back. The
 * read is quiet: a target with nothing to report is a status, not an error, so it answers `null` when
 * the backend has nothing to say.
 */

import { callBackend } from '../backendApi';
import type { BlinkPair, FrameStatusReport } from '../../types/backendTypes';

/**
 * Reads the status of every frame of a target, including the frames set aside.
 * @param targetId The target.
 * @return The report, or `null` when it could not be read.
 */
export async function fetchFrameStatuses(targetId: string): Promise<FrameStatusReport | null> {
    try {
        return (await callBackend('target:get_frame_statuses', { target_id: targetId }, { silent: true })) ?? null;
    } catch {
        return null;
    }
}

/**
 * Moves set-aside frames back into their target. This moves files, so only a person's click calls it.
 * @param targetId The target.
 * @param paths The set-aside frames, as the status report lists them.
 * @return The original paths of the frames that went back.
 */
export async function putBackFrames(targetId: string, paths: string[]): Promise<string[]> {
    const result = await callBackend('target:put_back_frames', { target_id: targetId, paths });
    return result?.restored ?? [];
}

/**
 * Asks for the Blink comparison of a frame: a typical frame of its session and one star in both frames.
 * The read is quiet, because a frame with nothing to compare against is a status, not an error.
 * @param targetId The target.
 * @param path The frame, as the status report lists it.
 * @return The pair (whose `found` is false, with a reason, when none could be made), or `null` when the backend could not answer.
 */
export async function fetchBlinkPair(targetId: string, path: string): Promise<BlinkPair | null> {
    try {
        return (await callBackend('quality:choose_blink_pair', { target: targetId, path }, { silent: true })) ?? null;
    } catch {
        return null;
    }
}
