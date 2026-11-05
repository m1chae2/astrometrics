/**
 * @fileoverview Builds the small "copying" text shown next to the camera state
 * while new frames are copied to this computer. The frame lists reload by
 * themselves when the backend reports new frames, so no message is shown.
 */

import type { FrameCopyPulse } from '../../common/types/backendTypes';

/** What the header shows while new frames are being copied. */
export interface FrameCopyDisplay {
  /** Short text such as "↓ 24/40" (or "↓" when the count is not known yet). */
  text: string;
  /** Sentence for the tooltip and for screen readers. */
  description: string;
}

/**
 * Turns the copy progress from the backend into text for the header.
 *
 * @param frameCopy - The copy progress from the telescope status, if any.
 * @param targetName - The target the telescope points at, used when the
 *   progress does not name one.
 * @returns The text to show, or null when no copy is running.
 */
export function describeFrameCopy(
  frameCopy: FrameCopyPulse | null | undefined,
  targetName: string | null | undefined
): FrameCopyDisplay | null {
  if (!frameCopy?.active) return null;
  const name = frameCopy.targetId ?? targetName ?? 'target';
  const base = `Copying new ${name} frames to this computer`;
  const hasCounts = typeof frameCopy.measured === 'number' && typeof frameCopy.total === 'number';
  if (!hasCounts) return { text: '↓', description: base };
  return {
    text: `↓ ${frameCopy.measured}/${frameCopy.total}`,
    description: `${base}: ${frameCopy.measured} of ${frameCopy.total} measured`,
  };
}
