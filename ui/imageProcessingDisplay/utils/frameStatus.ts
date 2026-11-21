/**
 * @file frameStatus.ts
 * @description Works out the File Browser's Status column and the star-shape chart's numbers from what
 * the backend reports. A frame is Included, Flagged (in the stack, but its stars are stretched far more
 * than the others), Set aside (the stacker moved it to an `_excluded` folder) or Not stacked yet (taken
 * after the last stack). Each status has a short word, a tone for its chip, and the reason shown when
 * the pointer rests on it. These are plain functions, so they can be tested without a screen.
 */

import type { FrameStatus, FrameStatusReport, StarShapeReview } from '../../common/types/backendTypes';
import type { StatusChipStatus } from '../../common/components/StatusChip';
import { sessionTypicalElongation } from './starShapeStats';

/** The four things the Status column can say. */
export type FrameStatusKind = 'included' | 'flagged' | 'set-aside' | 'not-stacked';

/** The kinds of set-aside frame the Status column names. */
export type SetAsideCategory = 'trailed' | 'cloud' | 'off field' | 'other';

/** What the File Browser shows and filters on for one frame. */
export interface FrameStatusInfo {
    kind: FrameStatusKind;
    /** The chip's word, for example "Set aside: cloud". */
    label: string;
    tone: StatusChipStatus;
    /** The reason shown on hover. */
    tooltip: string;
    /** The text the `status:` search filter looks in, for example "set-aside cloud". */
    filterText: string;
    /** Only for a set-aside frame: whether the app can put it back. */
    restorable: boolean;
    /** Only for a set-aside frame that cannot be put back: why. */
    restoreNote: string;
}

/** Said when nothing on record explains why a frame was set aside. */
export const NO_REASON_ON_RECORD = 'No record of why this frame was set aside.';

/**
 * Names the kind of a set-aside frame in the words the Status column uses.
 * @param kind The kind the stacker stored (`clouded`, `trailed` or `off_field`), or nothing.
 * @return `trailed`, `cloud`, `off field` or `other`.
 */
export function setAsideCategory(kind: string | null | undefined): SetAsideCategory {
    const text = (kind ?? '').toLowerCase();
    if (text.startsWith('trail')) return 'trailed';
    if (text.startsWith('cloud')) return 'cloud';
    if (text.includes('field')) return 'off field';
    return 'other';
}

/**
 * Builds the status of every frame the backend lists, with the review's flags added.
 * @param report The backend's per-frame statuses, or `null` when not read yet.
 * @param review The star-shape review, or `null` when not read yet.
 * @return The statuses by frame path.
 */
export function buildFrameStatusLookup(
    report: FrameStatusReport | null,
    review: StarShapeReview | null,
): Map<string, FrameStatusInfo> {
    const lookup = new Map<string, FrameStatusInfo>();
    const typical = sessionTypicalElongation(review?.series);
    const standouts = new Map((review?.standoutFrames ?? []).map((frame) => [frame.path, frame]));
    for (const frame of report?.frames ?? []) {
        lookup.set(frame.path, describeFrameStatus(frame, standouts.get(frame.path)?.elongation ?? null, typical, report?.stackedAt ?? null));
    }
    return lookup;
}

/**
 * Describes one frame's status.
 * @param frame The backend's status for the frame.
 * @param standoutElongation The frame's elongation when the review flags it, else `null`.
 * @param typical The session's typical elongation, or `null`.
 * @param stackedAt When the last stack was written, or `null` when the target has no stack.
 * @return The label, tone, hover text and filter text.
 */
export function describeFrameStatus(
    frame: FrameStatus,
    standoutElongation: number | null,
    typical: number | null,
    stackedAt: number | null,
): FrameStatusInfo {
    const restorable = frame.restorable ?? false;
    const restoreNote = frame.restoreNote ?? '';
    if (frame.state === 'set_aside') {
        const category = setAsideCategory(frame.kind);
        return {
            kind: 'set-aside',
            label: `Set aside: ${category}`,
            tone: 'neutral',
            tooltip: frame.reason ? frame.reason : NO_REASON_ON_RECORD,
            filterText: `set-aside ${category.replace(' ', '-')}`,
            restorable,
            restoreNote,
        };
    }
    if (frame.state === 'not_stacked') {
        return {
            kind: 'not-stacked',
            label: 'Not stacked yet',
            tone: 'info',
            tooltip: stackedAt === null ? 'This target has no stack yet.' : 'This frame was taken after the last stack was made.',
            filterText: 'not-stacked',
            restorable: false,
            restoreNote: '',
        };
    }
    if (standoutElongation !== null) {
        const typicalText = typical === null ? 'not known' : typical.toFixed(2);
        return {
            kind: 'flagged',
            label: 'Flagged',
            tone: 'warning',
            tooltip: `Stars stretched: elongation ${standoutElongation.toFixed(2)}, session typical ${typicalText}`,
            filterText: 'flagged',
            restorable: false,
            restoreNote: '',
        };
    }
    return {
        kind: 'included',
        label: 'Included',
        tone: 'ok',
        tooltip: 'This frame is in the last stack.',
        filterText: 'included',
        restorable: false,
        restoreNote: '',
    };
}

/**
 * Turns a status filter value into the form the filter text uses: lower case, with spaces as hyphens.
 * @param value What the person typed after `status:`, such as `Set aside`.
 * @return For example `set-aside`.
 */
export function normalizeStatusFilter(value: string): string {
    return value.trim().toLowerCase().replace(/\s+/g, '-');
}
