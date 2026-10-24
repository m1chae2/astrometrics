/**
 * @fileoverview Example sync status, shown until wave L1 adds the real read.
 *
 * The panel that shows this value must carry the "Example data" badge.
 */

/** When data last came from the telescope computer. Replace with the generated type in wave L. */
export interface ExampleSyncStatus {
    /** When the logs were last synced, ISO 8601 text, or null for never. */
    lastLogSync: string | null;
    /** When the frames were last synced, ISO 8601 text, or null for never. */
    lastFrameSync: string | null;
    /** The time the shown data is as of, ISO 8601 text, or null for none. */
    dataAsOf: string | null;
}

/** Example sync status. */
export const exampleSyncStatus: ExampleSyncStatus = {
    lastLogSync: '2026-10-23T23:40:00-06:00',
    lastFrameSync: '2026-10-23T23:35:00-06:00',
    dataAsOf: '2026-10-23T23:40:00-06:00',
};
