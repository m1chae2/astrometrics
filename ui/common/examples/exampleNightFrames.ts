/**
 * @fileoverview Example per-frame rows for a night, shown until wave L1 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge.
 */

/** One frame row. Replace with the generated type in wave L. */
export interface ExampleNightFrame {
    /** The frame's id. */
    frameId: string;
    /** The time the frame was taken, as ISO 8601 text. */
    time: string;
    /** The target the frame is of. */
    target: string;
    /** The capture setting, as a short label. */
    captureSetting: string;
    /** The star width in arcseconds, or null when not measured. */
    starWidthArcsec: number | null;
    /** The sky rate in electrons per second per pixel, or null when not measured. */
    skyRate: number | null;
    /** The frame's status word. */
    status: 'not_yet_judged' | 'flagged_by_live_status' | 'set_aside' | 'in_stack';
    /** The ids of the measurement rows behind this frame. */
    rowIds: number[];
}

/** Example frame rows. */
export const exampleNightFrames: ExampleNightFrame[] = [
    {
        frameId: 'frame-001',
        time: '2026-10-23T22:10:00-06:00',
        target: 'M 81',
        captureSetting: 'L 60 s',
        starWidthArcsec: 2.9,
        skyRate: 0.42,
        status: 'in_stack',
        rowIds: [101, 102],
    },
    {
        frameId: 'frame-002',
        time: '2026-10-23T22:11:10-06:00',
        target: 'M 81',
        captureSetting: 'L 60 s',
        starWidthArcsec: 3.8,
        skyRate: 0.43,
        status: 'flagged_by_live_status',
        rowIds: [103, 104],
    },
    {
        frameId: 'frame-003',
        time: '2026-10-23T22:12:20-06:00',
        target: 'M 81',
        captureSetting: 'L 60 s',
        starWidthArcsec: null,
        skyRate: null,
        status: 'not_yet_judged',
        rowIds: [],
    },
];
