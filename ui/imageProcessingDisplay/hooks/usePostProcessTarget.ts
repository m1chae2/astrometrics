/**
 * @file usePostProcessTarget.ts
 * @description Makes a target's stack pictures again without stacking, for the "Post Process Target"
 * entry of the Stack Frames split button. It remakes the imaging stack's picture, then the spectral
 * stack's picture, one job after the other, using the user's current settings. After each picture is
 * made, it tells the Image Viewer to read it again.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { emit as emitEvent } from '../../common/utils/eventBus';
import {
    previewRemakeResultOf,
    STACK_PICTURE_REMADE_EVENT,
    startPreviewRemake,
    type StackKind,
} from '../../common/services/imaging/stackReviewService';
import { cancelProcessing } from '../../common/services/imaging/processingService';
import type { PreviewRemakeResult } from '../../common/types/backendTypes';
import { useOnValueChange } from './useOnValueChange';
import { useReviewJob, type ReviewJobState } from './useReviewJob';

/**
 * Follows the picture remake of one stack kind and tells the viewer when the picture is ready.
 *
 * @param targetId The target.
 * @param kind The imaging or spectral stack.
 * @param onShowPicture Called with the stack's path when the new picture is on display.
 * @return The job state and a function that starts the remake with the user's settings.
 */
export function usePreviewRemake(
    targetId: string,
    kind: StackKind,
    onShowPicture?: (stackPath: string) => void,
): { state: ReviewJobState<PreviewRemakeResult>; start: () => void } {
    const { state, run } = useReviewJob<PreviewRemakeResult>({
        targetId,
        jobType: 'preview',
        readResult: previewRemakeResultOf,
    });

    // When a picture is ready and on display, tell the viewer to read it again.
    const announcedRef = useRef<PreviewRemakeResult | null>(null);
    useEffect(() => {
        const result = state.result;
        if (!result || announcedRef.current === result) return;
        announcedRef.current = result;
        if (result.shown_in_viewer) {
            emitEvent('targetsUpdated', { id: targetId });
            emitEvent(STACK_PICTURE_REMADE_EVENT, { targetId, stackPath: result.stack_path });
            onShowPicture?.(result.stack_path);
        }
    }, [state.result, targetId, onShowPicture]);

    const start = useCallback(() => {
        void run(() => startPreviewRemake(targetId, kind));
    }, [run, targetId, kind]);

    return { state, start };
}

/** What the hook reports to the screen. */
export interface PostProcessTarget {
    /** True while a remake is starting or running, or another one is waiting its turn. */
    busy: boolean;
    /** Why the last run stopped early, or `null`. */
    errorMessage: string | null;
    /** Remakes the pictures of the given stacks, in order. */
    start: (kinds: StackKind[]) => void;
    /** Cancels the running remake and drops the ones still waiting. */
    cancel: () => void;
}

/**
 * Runs the picture remakes of a target one after the other.
 *
 * @param targetId The selected target.
 * @param onShowPicture Called with the stack's path when a new picture is on display.
 * @return The busy flag, the last error and the function that starts a run.
 */
export function usePostProcessTarget(
    targetId: string | undefined,
    onShowPicture?: (stackPath: string) => void,
): PostProcessTarget {
    const id = targetId ?? '';
    const imaging = usePreviewRemake(id, 'imaging', onShowPicture);
    const spectral = usePreviewRemake(id, 'spectral', onShowPicture);
    // The kinds still to run. The first one is running now.
    const [queue, setQueue] = useState<StackKind[]>([]);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const runners = { imaging, spectral };

    useOnValueChange(id, () => {
        setQueue([]);
        setErrorMessage(null);
    });

    // When the running remake ends, start the next one, or stop if it failed.
    const headPhase = queue.length > 0 ? runners[queue[0]].state.phase : 'idle';
    const headMessage = queue.length > 0 ? runners[queue[0]].state.message : null;
    useEffect(() => {
        if (queue.length === 0) return;
        if (headPhase === 'cancelled') {
            // The user cancelled: stop quietly, without an error and without the next picture.
            setQueue([]);
        } else if (headPhase === 'failed') {
            setErrorMessage(headMessage ?? 'The picture could not be made.');
            setQueue([]);
        } else if (headPhase === 'done') {
            const rest = queue.slice(1);
            setQueue(rest);
            if (rest.length > 0) runners[rest[0]].start();
        }
        // The queue and the running phase decide when this runs.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [queue, headPhase]);

    const start = useCallback((kinds: StackKind[]) => {
        if (kinds.length === 0) return;
        setErrorMessage(null);
        setQueue(kinds);
        (kinds[0] === 'imaging' ? imaging : spectral).start();
        // The two starters are stable for one target.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [imaging.start, spectral.start]);

    const cancel = useCallback(() => {
        // Drop the waiting kinds at once, so a cancelled remake does not start the next one.
        setQueue((current) => current.slice(0, 1));
        void cancelProcessing(id);
    }, [id]);

    return { busy: queue.length > 0, errorMessage, start, cancel };
}
