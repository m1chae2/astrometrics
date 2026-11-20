/**
 * @file displayMode.ts
 * @description The two ways the FITS viewer can show an image, and the words that describe them.
 */

/**
 * How the viewer draws an image.
 *
 * - `linear`: brightness in proportion to the pixel value, between black and white points set from the sky.
 * - `auto-stretch`: the library's automatic midtone stretch, which brightens faint detail.
 */
export type DisplayMode = 'linear' | 'auto-stretch';

/** What each display mode is called and what it is good for. The tooltips are shown beside the Display control. */
export const DISPLAY_MODE_OPTIONS: ReadonlyArray<{ id: DisplayMode; label: string; title: string }> = [
  {
    id: 'linear',
    label: 'Linear',
    title: 'Brightness shown in proportion, with the range set from the sky noise, so equal noise looks equally grainy on any image. Use this to judge star sizes, halos and pairs.',
  },
  {
    id: 'auto-stretch',
    label: 'Auto Stretch',
    title: 'Brightens faint detail. Star sizes and halos look larger than they are.',
  },
];

/** The display mode a viewer starts in. */
export const DEFAULT_DISPLAY_MODE: DisplayMode = 'auto-stretch';
