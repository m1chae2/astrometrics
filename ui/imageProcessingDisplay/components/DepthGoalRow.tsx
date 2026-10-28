/**
 * @file DepthGoalRow.tsx
 * @description The "Integration Goal" row of the Image Processing Display's Data tab.
 * The person picks how deep they want the image (Quick look, Standard or Deep).
 * The row then says how close the stack is and how much more time it needs.
 */
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import { DepthGoalState, DepthKind } from '../../common/types/backendTypes';
import type { DepthGoalStatus } from '../../common/types/backendTypes';

type DepthLevelChoice = 'quick_look' | 'good' | 'deep';

interface DepthGoalRowProps {
    /** The selected target's id. With none, the row shows nothing. */
    targetId: string | null;
    /** Changes when a stack finishes or the library reloads, so the row reads again. */
    refreshKey?: string | number;
}

/**
 * Writes the goal and current depth in words with units, for the info tip.
 * @param status The depth goal status.
 * @return The sentence, or an empty string when there is no goal depth.
 */
export const describeDepthDetail = (status: DepthGoalStatus): string => {
    if (status.goalDepth == null) return '';
    const now = status.currentDepth != null ? status.currentDepth.toFixed(1) : 'not measured yet';
    if (status.depthKind === DepthKind.SURFACE_BRIGHTNESS) {
        return `Goal: ${status.goalDepth.toFixed(1)} mag/arcsec² (surface brightness, 3σ over 10″). Now: ${now}.`;
    }
    return `Goal: ${status.goalDepth.toFixed(1)} mag (faintest star detected at 5σ). Now: ${now}.`;
};

/**
 * Shows the depth goal dropdown and the status line for one target.
 * When the kind of object has no depth goal, the row says so in muted text,
 * so a new user knows the goal was not forgotten.
 */
export const DepthGoalRow: React.FC<DepthGoalRowProps> = ({ targetId, refreshKey }) => {
    const queryClient = useQueryClient();
    const queryKey = [...qualityQueryKeys.targetReport(targetId ?? ''), 'depth-goal', refreshKey ?? ''];
    const { data: status, isLoading, isError } = useQuery({
        queryKey,
        queryFn: () => callBackend('advisory:depth_goal', { target: targetId as string }),
        enabled: Boolean(targetId),
    });
    const setLevel = useMutation({
        mutationFn: (level: DepthLevelChoice) =>
            callBackend('advisory:set_depth_goal', { target: targetId as string, level }),
        onSuccess: (updated) => queryClient.setQueryData(queryKey, updated),
    });

    if (!targetId) return null;
    if (isLoading) {
        return <div className="depth-goal-row depth-goal-row--muted">Loading depth goal...</div>;
    }
    if (isError || !status) {
        return <div className="depth-goal-row depth-goal-row--error" role="alert">Could not load the depth goal.</div>;
    }

    const shown: DepthGoalStatus = status;
    if (shown.state === DepthGoalState.NO_DEPTH_GOAL) {
        return (
            <div className="depth-goal-row depth-goal-row--muted" title={shown.reason || undefined}>
                No depth goal for this kind of object.
            </div>
        );
    }

    const detail = describeDepthDetail(shown);
    return (
        <div className="depth-goal-row">
            <div className="depth-goal-row__choice">
                <span className="stacked-image-panel__label">Integration Goal:</span>
                {detail && (
                    <span className="depth-goal-row__info" title={detail} aria-label={detail} role="img">ⓘ</span>
                )}
                <select
                    className="file-browser__camera-select"
                    aria-label="Integration Goal"
                    value={shown.level}
                    disabled={setLevel.isPending}
                    onChange={(event) => setLevel.mutate(event.target.value as DepthLevelChoice)}
                >
                    {(shown.options ?? []).map((option) => (
                        <option key={option.level} value={option.level}>{option.label}</option>
                    ))}
                </select>
            </div>
            <div className="depth-goal-row__message" title={shown.reason || undefined}>{shown.message}</div>
            {setLevel.isError && (
                <div className="depth-goal-row--error" role="alert">Could not save the depth goal.</div>
            )}
        </div>
    );
};
