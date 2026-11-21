/**
 * @file StarShapesSummary.tsx
 * @description The "Frame status timeline" block of the Frame Analysis Data tab. It shows when each frame
 * of the selected target was taken and what happened to it (included, flagged, or set aside and why), and
 * nothing else. Nothing is measured here. The times and statuses come from what was stored when the
 * frames were measured. A target with nothing to show gets a short status, not an error.
 */
import React, { useMemo } from 'react';
import type { FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';
import type { FrameStatusInfo } from '../utils/frameStatus';
import { StarShapeChart, buildStarShapeChart } from './StarShapeChart';

/** Said when the review has nothing to show for the target. */
export const NOT_MEASURED_NOTE = 'No frames to show for this target yet.';

interface StarShapesSummaryProps {
    /** The review, or `null` when it is not available. */
    review: StarShapeReview | null;
    /** The frame picked in the File Browser, or `null`. */
    selectedPath: string | null;
    /** The backend's per-frame statuses (including set-aside frames), or `null` until read. */
    frameReport?: FrameStatusReport | null;
    /** The File Browser's status of each frame by path. */
    frameStatuses?: ReadonlyMap<string, FrameStatusInfo>;
    /** Called with a frame's path when its mark is clicked. */
    onSelectFrame: (path: string) => void;
}

/**
 * Shows the frame status timeline, or a short status when there are no frames.
 * @param props See {@link StarShapesSummaryProps}.
 * @return The block, or `null` when there is no target review to show.
 */
export const StarShapesSummary: React.FC<StarShapesSummaryProps> = ({ review, selectedPath, frameReport = null, frameStatuses, onSelectFrame }) => {
    const flaggedPaths = useMemo(() => new Set((review?.standoutFrames ?? []).map((frame) => frame.path)), [review]);
    const frames = frameReport?.frames ?? null;
    const measured = useMemo(
        () => buildStarShapeChart(review?.series ?? [], flaggedPaths, null, frames, frameStatuses).drawnCount > 0,
        [review, flaggedPaths, frames, frameStatuses],
    );
    if (!review) return null;
    return (
        <div className="stacked-image-panel-container" data-testid="star-shapes-summary">
            <div className="stack-summary">
                <span className="stacked-image-panel__label">Frame status timeline</span>
                {measured ? (
                    <StarShapeChart
                        series={review.series ?? []}
                        flaggedPaths={flaggedPaths}
                        selectedPath={selectedPath}
                        frames={frames}
                        statuses={frameStatuses}
                        onSelectFrame={onSelectFrame}
                    />
                ) : (
                    <p data-testid="star-shape-status">{NOT_MEASURED_NOTE}</p>
                )}
            </div>
        </div>
    );
};
