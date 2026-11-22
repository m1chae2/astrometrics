/**
 * @file useCheckFrames.ts
 * @description Runs the "Check Frames" entry of the Stack Frames split button. It asks the backend
 * what stacking would do with the frames the Stack Frames click would send (the ticked frames, or
 * every listed frame when none is ticked): which frames it would set aside and why, and which
 * darks, bias and flats each exposure group would use. Nothing is stacked or changed. The check
 * reads every frame, so it runs as a background job that this hook follows to the end.
 */
import { useCallback } from 'react';
import { startStackCheck, stackPredictionOf } from '../../common/services/imaging/stackReviewService';
import type { StackPrediction } from '../../common/types/backendTypes';
import { useReviewJob, type ReviewJobPhase } from './useReviewJob';

/** What the hook reports to the screen. */
export interface CheckFrames {
    /** True while the check is starting or running. */
    busy: boolean;
    /** Where the check stands. */
    phase: ReviewJobPhase;
    /** The last prediction, or `null`. */
    prediction: StackPrediction | null;
    /** Why the last check failed, or `null`. */
    errorMessage: string | null;
    /** Starts a check of the given frames (file paths). */
    start: (imageFiles: string[]) => void;
}

/**
 * Checks what stacking would do with a target's frames, without stacking.
 *
 * @param targetId The selected target.
 * @return The busy flag, the last prediction, the last error and the function that starts a check.
 */
export function useCheckFrames(targetId: string | undefined): CheckFrames {
    const { state, run } = useReviewJob<StackPrediction>({
        targetId: targetId ?? '',
        jobType: 'diagnostics',
        readResult: stackPredictionOf,
    });

    const start = useCallback((imageFiles: string[]) => {
        if (!targetId) return;
        void run(() => startStackCheck(targetId, imageFiles));
    }, [targetId, run]);

    return {
        busy: state.phase === 'starting' || state.phase === 'running',
        phase: state.phase,
        prediction: state.phase === 'done' ? state.result : null,
        errorMessage: state.phase === 'failed' ? state.message : null,
        start,
    };
}
