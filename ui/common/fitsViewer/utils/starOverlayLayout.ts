/**
 * @file starOverlayLayout.ts
 * @description Pure helpers for the star overlay on the FITS viewer: where a
 * star lands on screen, which stars are in view, which of them get a text
 * label (a fixed budget, so zooming in shows more labels per area), and which
 * star the pointer is over.
 */
import { AstrometryOverlayStar } from '../../services/astronomyService';

/** How many stars get a text label at one time. Every star still gets a circle. */
export const STAR_LABEL_BUDGET = 35;

/** Pointer distance, in screen pixels, within which a star circle counts as hovered. */
export const STAR_HIT_RADIUS_PX = 8;

/** Rough label height in screen pixels, used to keep labels from overlapping. */
const LABEL_HEIGHT_PX = 24;

/** How a star's image position maps to the screen. */
export interface StarScreenTransform {
  /** Screen pixels per image pixel, horizontally. */
  scaleX: number;
  /** Screen pixels per image pixel, vertically. */
  scaleY: number;
  /** Screen x of the image's left edge. */
  panX: number;
  /** Screen y of the image's top edge. */
  panY: number;
  /** Width of the viewport in screen pixels. */
  viewWidth: number;
  /** Height of the viewport in screen pixels. */
  viewHeight: number;
}

/**
 * Converts a star's image position to a screen position.
 *
 * @param star The overlay star.
 * @param transform The current image-to-screen mapping.
 * @returns The screen x and y.
 */
export function starScreenPosition(
  star: AstrometryOverlayStar,
  transform: StarScreenTransform
): { x: number; y: number } {
  return {
    x: transform.panX + star.x * transform.scaleX,
    y: transform.panY + star.y * transform.scaleY
  };
}

/**
 * Tells whether a screen position is inside the viewport.
 *
 * @param position Screen x and y.
 * @param transform The current image-to-screen mapping (for the viewport size).
 * @returns True when the position is in view.
 */
export function isInView(position: { x: number; y: number }, transform: StarScreenTransform): boolean {
  return position.x >= 0 && position.y >= 0
    && position.x <= transform.viewWidth && position.y <= transform.viewHeight;
}

/**
 * Chooses which stars get a text label.
 *
 * Only stars inside the viewport are considered. They are taken in the order
 * given (the backend sends the most trustworthy first), up to the budget. A
 * star whose label would cover an already chosen label is skipped. The
 * selected star, when in view, is always labeled first.
 *
 * @param stars All overlay stars, most trustworthy first.
 * @param transform The current image-to-screen mapping.
 * @param estimateLabelWidth Gives a star's label width in screen pixels.
 * @param budget The most labels to choose.
 * @param isSelected Tells whether a star is the selected one.
 * @returns The ids of the stars to label.
 */
export function chooseLabeledStarIds(
  stars: readonly AstrometryOverlayStar[],
  transform: StarScreenTransform,
  estimateLabelWidth: (star: AstrometryOverlayStar) => number,
  budget: number = STAR_LABEL_BUDGET,
  isSelected: (star: AstrometryOverlayStar) => boolean = () => false
): Set<string> {
  const chosen = new Set<string>();
  const boxes: Array<{ left: number; right: number; top: number; bottom: number }> = [];
  const ordered = [
    ...stars.filter((star) => isSelected(star)),
    ...stars.filter((star) => !isSelected(star))
  ];
  for (const star of ordered) {
    if (chosen.size >= budget) break;
    const position = starScreenPosition(star, transform);
    if (!isInView(position, transform)) continue;
    const box = {
      left: position.x,
      right: position.x + 20 + estimateLabelWidth(star),
      top: position.y - LABEL_HEIGHT_PX / 2,
      bottom: position.y + LABEL_HEIGHT_PX / 2
    };
    const overlaps = boxes.some(
      (other) => box.left < other.right && box.right > other.left
        && box.top < other.bottom && box.bottom > other.top
    );
    if (overlaps && !isSelected(star)) continue;
    boxes.push(box);
    chosen.add(star.id);
  }
  return chosen;
}

/**
 * Finds the star circle nearest the pointer.
 *
 * @param stars All overlay stars.
 * @param transform The current image-to-screen mapping.
 * @param pointerX Pointer x in viewport pixels.
 * @param pointerY Pointer y in viewport pixels.
 * @param radius Largest distance, in screen pixels, that counts as a hit.
 * @returns The nearest star within the radius, or null.
 */
export function findStarNearPointer(
  stars: readonly AstrometryOverlayStar[],
  transform: StarScreenTransform,
  pointerX: number,
  pointerY: number,
  radius: number = STAR_HIT_RADIUS_PX
): AstrometryOverlayStar | null {
  let best: AstrometryOverlayStar | null = null;
  let bestDistance = radius * radius;
  for (const star of stars) {
    const position = starScreenPosition(star, transform);
    const dx = position.x - pointerX;
    const dy = position.y - pointerY;
    const distance = dx * dx + dy * dy;
    if (distance <= bestDistance) {
      bestDistance = distance;
      best = star;
    }
  }
  return best;
}
