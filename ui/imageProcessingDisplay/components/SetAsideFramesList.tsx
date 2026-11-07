/**
 * @file SetAsideFramesList.tsx
 * @description The list behind the "Set aside before stacking" count in the Stack Summary block.
 * It says, for each frame the stacker moved into an `_excluded` folder, why it was set aside:
 * a plain sentence, the measurement behind it, the file and the exposure. It only reads. A
 * frame whose reason was never recorded says so. There is no button to put a frame back.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Skeleton } from '../../common/components/Skeleton';
import { fetchSetAsideFrames } from '../../common/services/imaging/stackReviewService';
import type { SetAsideFrameRecord } from '../../common/types/backendTypes';
import { formatExposureSeconds } from '../utils/stackFormatting';

interface SetAsideFramesListProps {
    /** The selected target's id. */
    targetId: string;
    /** How many frames the stack summary says were set aside, for the heading. */
    count: number;
    /** Changes when a stack finishes, so the list is read again. */
    refreshKey?: string | number;
}

/** Said for a frame that nothing on record explains. */
export const NO_RECORD_OF_WHY = 'No record of why this frame was set aside.';

/**
 * Says in plain words why a frame was set aside, from the kind the stacker stored.
 *
 * The stacker stores only two kinds. "trailed" is used for smeared stars and also for a frame
 * with far fewer stars than usual under a normal sky, so its wording covers both. The stored
 * kind is not changed, and the measurement text under the sentence tells which one it was.
 * @param kind The stored kind, or nothing when no source names one.
 * @return A sentence, or the no-record sentence when there is no kind.
 */
export const describeSetAsideKind = (kind: string | null | undefined): string => {
    if (kind === 'clouded') return 'Clouds: far fewer stars than usual and a brighter sky.';
    if (kind === 'trailed') return 'Stars smeared, or far fewer stars than usual.';
    return NO_RECORD_OF_WHY;
};

/**
 * Tells whether nothing on record says why a frame was set aside.
 * @param frame The frame's record.
 * @return `true` when the reason is missing.
 */
export const hasNoRecordedReason = (frame: SetAsideFrameRecord): boolean =>
    !frame.kind && !frame.reason;

/**
 * Writes the line that tells a person the frame cannot be put back by the app.
 * @param frame The frame's record.
 * @return The note, or `null` when the frame can be restored.
 */
export const restoreNoteOf = (frame: SetAsideFrameRecord): string | null =>
    frame.restorable === false ? (frame.restoreNote || 'It cannot be put back automatically.') : null;

/**
 * Shows the set-aside frames of one target, read when the list is first opened.
 */
export const SetAsideFramesList: React.FC<SetAsideFramesListProps> = ({ targetId, count, refreshKey }) => {
    const { data: frames, isLoading, isError } = useQuery<SetAsideFrameRecord[]>({
        queryKey: ['setAsideFrames', targetId, refreshKey ?? ''],
        queryFn: () => fetchSetAsideFrames(targetId),
        enabled: Boolean(targetId),
    });

    if (isLoading) {
        return <Skeleton variant="list" lines={1} count={3} label="Reading the set-aside frames" />;
    }
    if (isError) {
        return (
            <div className="stack-summary--error" role="alert">
                Could not read the set-aside frames.
            </div>
        );
    }
    if (!frames || frames.length === 0) {
        return (
            <div className="stack-summary--muted">
                No set-aside frames were found in this target's folders, though the summary counts {count}.
            </div>
        );
    }

    return (
        <ul className="set-aside-list" aria-label="Frames set aside before stacking">
            {frames.map((frame) => {
                const note = restoreNoteOf(frame);
                return (
                    <li key={frame.path} className="set-aside-list__item">
                        <div>{describeSetAsideKind(frame.kind)}</div>
                        {frame.reason && <div className="stack-summary--muted">{frame.reason}</div>}
                        <div className="stack-summary--muted">
                            {frame.file}
                            {frame.exposureSeconds != null && <>, {formatExposureSeconds(frame.exposureSeconds)} exposure</>}
                        </div>
                        {note && <div className="stack-summary__flag">{note}</div>}
                    </li>
                );
            })}
        </ul>
    );
};
