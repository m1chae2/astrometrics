/**
 * @file StarShapesSummary.tsx
 * @description The "Star shapes" block of the Frame Analysis Data tab. It shows the stored star-shape
 * verdicts of the selected target in plain words: one line that says whether the stars are round and,
 * when they are stretched, whether they point one way; a note on the corner star widths marked "not yet
 * validated"; and the chart of stretch by frame. Nothing is measured here. The numbers come from the
 * measurements stored when the frames were measured. A target with nothing measured shows a short
 * status, not an error.
 */
import React, { useMemo } from 'react';
import { StatusChip } from '../../common/components/StatusChip';
import type { ShapeVerdict, StarShapeReview } from '../../common/types/backendTypes';
import { StarShapeChart } from './StarShapeChart';

/** Said when the review has nothing to show for the target. */
export const NOT_MEASURED_NOTE = 'Star shapes have not been measured for this target yet.';

/** What the chip beside the corner note says, since the corner numbers are not yet checked against real data. */
export const CORNERS_NOT_VALIDATED_LABEL = 'Not yet validated';

/** The verdict states that mean a verdict has something to say about the direction of the stretch. */
const DIRECTION_STATES = new Set(['shared', 'mixed']);

/**
 * Writes the one-line verdict: whether the stars are round, then (only for stretched stars) the direction.
 * @param review The star-shape review.
 * @return The line, or `null` when the review has no roundness verdict.
 */
export function describeStarShapeVerdict(review: StarShapeReview | null): string | null {
    if (!review || review.roundness.state === 'not_measured') return null;
    const direction = DIRECTION_STATES.has(review.direction.state) ? ` ${review.direction.sentence}` : '';
    return `${review.roundness.sentence}${direction}`;
}

/**
 * Writes the corner note from the corner verdict's numbers.
 * @param corners The review's corner verdict.
 * @return A sentence such as "Corner stars are 1.08 times as wide as center stars.", or `null`.
 */
export function describeCorners(corners: ShapeVerdict): string | null {
    const ratio = corners.numbers?.corner_over_center;
    if (corners.state === 'not_measured' || typeof ratio !== 'number') return null;
    return `Corner stars are ${ratio.toFixed(2)} times as wide as center stars.`;
}

interface StarShapesSummaryProps {
    /** The review, or `null` when it is not available. */
    review: StarShapeReview | null;
    /** The frame picked in the File Browser, or `null`. */
    selectedPath: string | null;
    /** Called with a frame's path when its dot is clicked. */
    onSelectFrame: (path: string) => void;
}

/**
 * Shows the star-shape verdict line, the corner note and the stretch-by-frame chart.
 * @param props See {@link StarShapesSummaryProps}.
 * @return The block, or `null` when there is no target review to show.
 */
export const StarShapesSummary: React.FC<StarShapesSummaryProps> = ({ review, selectedPath, onSelectFrame }) => {
    const flaggedPaths = useMemo(() => new Set((review?.standoutFrames ?? []).map((frame) => frame.path)), [review]);
    if (!review) return null;
    const verdict = describeStarShapeVerdict(review);
    const corners = describeCorners(review.corners);
    return (
        <div className="star-shapes-summary" data-testid="star-shapes-summary">
            <h4 className="star-shapes-summary__title">Star shapes</h4>
            <div data-testid="star-shape-verdict">{verdict ?? NOT_MEASURED_NOTE}</div>
            {corners && (
                <div className="star-shapes-summary__corners" data-testid="star-shape-corners">
                    <span>{corners}</span>
                    <StatusChip status="neutral" label={CORNERS_NOT_VALIDATED_LABEL} title={review.corners.sentence} />
                </div>
            )}
            <StarShapeChart
                series={review.series ?? []}
                flaggedPaths={flaggedPaths}
                selectedPath={selectedPath}
                onSelectFrame={onSelectFrame}
            />
        </div>
    );
};
