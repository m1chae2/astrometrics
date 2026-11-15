/**
 * @fileoverview Helpers for picking one optic (a focal length) in the frame list.
 *
 * One camera can shoot the same target through two optics, for example a
 * 300 mm lens and a 405 mm telescope. The stacking library refuses to mix
 * them, so the frame list lets the user pick one. These helpers are plain
 * functions with no React in them, so they are easy to test.
 */

/** The part of a frame record these helpers read. */
export interface OpticFrame {
  path: string;
  camera?: string;
  focalLengthMm?: number | null;
}

/** One choice in the Optic picker. */
export interface OpticOption {
  /** The focal length in whole millimeters, as text (for example "405"). */
  id: string;
  /** What the user sees (for example "405 mm (21 frames)"). */
  label: string;
}

/** What the files to stack look like in terms of optics. */
export interface MultiOpticStatus {
  /** True when the files come from more than one optic. */
  hasMultiple: boolean;
  /** The distinct focal lengths in whole millimeters, smallest first. */
  focalLengths: number[];
}

/**
 * Round a focal length to whole millimeters, the same way the library does.
 *
 * @param focalLengthMm - The focal length, or null or undefined if unknown.
 * @returns The rounded value, or null when the frame has no usable focal length.
 */
export const roundFocalLength = (focalLengthMm: number | null | undefined): number | null => {
  if (focalLengthMm === null || focalLengthMm === undefined || !Number.isFinite(focalLengthMm)) return null;
  return Math.round(focalLengthMm);
};

/**
 * Count frames for each distinct (rounded) focal length.
 *
 * @param files - The frames to count. Frames with no focal length are skipped.
 * @returns A map from focal length to frame count.
 */
const countFramesByFocalLength = (files: OpticFrame[]): Map<number, number> => {
  const counts = new Map<number, number>();
  files.forEach(file => {
    const focalLength = roundFocalLength(file.focalLengthMm);
    if (focalLength !== null) counts.set(focalLength, (counts.get(focalLength) ?? 0) + 1);
  });
  return counts;
};

/**
 * Build the choices for the Optic picker.
 *
 * @param files - Every frame of the target.
 * @returns One option per distinct focal length, smallest first. The list is
 *   empty when the frames have one optic or none, so the picker stays hidden.
 */
export const buildOpticOptions = (files: OpticFrame[]): OpticOption[] => {
  const counts = countFramesByFocalLength(files);
  if (counts.size < 2) return [];
  return Array.from(counts.entries())
    .sort((first, second) => first[0] - second[0])
    .map(([focalLength, count]) => ({
      id: String(focalLength),
      label: `${focalLength} mm (${count} ${count === 1 ? 'frame' : 'frames'})`,
    }));
};

/**
 * Keep only the frames from the chosen optic.
 *
 * @param files - The frames to filter.
 * @param selectedOptic - The chosen focal length as text, or '' for all optics.
 * @returns All frames when nothing is chosen, otherwise the frames whose
 *   rounded focal length matches.
 */
export const filterFilesByOptic = <T extends OpticFrame>(files: T[], selectedOptic: string): T[] => {
  if (!selectedOptic) return files;
  const wanted = Number(selectedOptic);
  return files.filter(file => roundFocalLength(file.focalLengthMm) === wanted);
};

/**
 * Pick the optic to select by default: the one with the most frames for the camera.
 *
 * @param files - Every frame of the target.
 * @param camera - The selected camera, or 'All' to count every camera.
 * @returns The focal length as text, or '' when that camera's frames have
 *   fewer than two optics (then no choice is needed).
 */
export const chooseDefaultOptic = (files: OpticFrame[], camera: string = 'All'): string => {
  const framesOfCamera = camera === 'All' ? files : files.filter(file => file.camera === camera);
  if (buildOpticOptions(framesOfCamera).length === 0) return '';
  let bestFocalLength: number | null = null;
  let bestCount = 0;
  countFramesByFocalLength(framesOfCamera).forEach((count, focalLength) => {
    // On a tie the smaller focal length wins, so the answer does not depend on frame order.
    if (count > bestCount || (count === bestCount && bestFocalLength !== null && focalLength < bestFocalLength)) {
      bestFocalLength = focalLength;
      bestCount = count;
    }
  });
  return bestFocalLength === null ? '' : String(bestFocalLength);
};

/**
 * Keep only the frames from the chosen camera.
 *
 * @param files - The frames to filter.
 * @param camera - The camera name, or 'All' to keep every frame.
 * @returns The frames of that camera.
 */
export const filterFilesByCamera = <T extends OpticFrame>(files: T[], camera: string): T[] => {
  if (camera === 'All') return files;
  return files.filter(file => file.camera === camera);
};

/**
 * Say whether the files to stack come from more than one optic.
 *
 * @param files - The files that a Stack action would send.
 * @returns Whether there is more than one optic, and which focal lengths.
 */
export const getMultiOpticStatus = (files: OpticFrame[]): MultiOpticStatus => {
  const focalLengths = Array.from(countFramesByFocalLength(files).keys()).sort((first, second) => first - second);
  return { hasMultiple: focalLengths.length > 1, focalLengths };
};
