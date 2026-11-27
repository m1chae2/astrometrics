/**
 * @file targetReadQueryPolicy.ts
 * @description The keep-and-reuse rules for the slow reads the Image Processing display makes about
 * one target: its star-shape verdicts, its frame statuses and its grouped light-frame summary.
 *
 * The display is hidden and shown again often, and hiding it stops its effects. Without a cache,
 * every return to the display, and every click back to a target already opened, asked the backend
 * again. The star-shape read alone can take seconds. These reads now go through React Query, and
 * each key names only its target (see `common/queries/targetReadQueryKeys.ts`):
 *
 * - The same target shows the kept answer at once, with no new call.
 * - A change to one target (a restack, new frames, an edit) marks only that target's kept reads as
 *   old (`invalidateTargetReads`), so it reads again. The other targets keep theirs. A change that
 *   names no target marks every target's reads as old. Putting a frame back reads the frame
 *   statuses again on purpose.
 * - A measurement job (`quality:measured`) refreshes the quality reads of its target.
 * - A failed call is not kept. The read throws, React Query does not retry it, and the next visit
 *   asks again. The hooks show a failure the same as "nothing yet", never as an error.
 */

/** How long a kept answer counts as fresh, and how long an unused one is kept: 30 minutes. */
export const TARGET_READ_KEEP_MS = 30 * 60 * 1000;

/** Query options shared by the target reads of the Image Processing display. */
export const TARGET_READ_QUERY_OPTIONS = {
    staleTime: TARGET_READ_KEEP_MS,
    gcTime: TARGET_READ_KEEP_MS,
    refetchOnWindowFocus: false,
    // A failed read is asked again on the next visit or after the next change to its target, not in a loop.
    retry: false,
} as const;
