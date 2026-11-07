/**
 * @fileoverview Builds the small "Downloading" or "Analyzing" text shown next to
 * the camera state while new frames are copied to this computer and measured. The frame lists reload by
 * themselves when the backend reports new frames, so no message is shown.
 */

import type { FrameCopyPulse } from '../../common/types/backendTypes';

/** What the header shows while new frames are being copied. */
export interface FrameCopyDisplay {
  /** Short text such as "Analyzing 24/40" (or "Downloading" while the files copy). */
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
  // No measured count yet means the files are still copying.
  if (typeof frameCopy.measured !== 'number') {
    return { text: 'Downloading', description: `Copying new ${name} frames to this computer` };
  }
  // Measuring has started but the job has not said how many frames yet.
  if (typeof frameCopy.total !== 'number') {
    return { text: 'Analyzing', description: `Measuring the new ${name} frames` };
  }
  return {
    text: `Analyzing ${frameCopy.measured}/${frameCopy.total}`,
    description: `Measuring the new ${name} frames: ${frameCopy.measured} of ${frameCopy.total} done`,
  };
}
