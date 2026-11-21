/**
 * @file starOverlayLayout.ts
 * @description Pure helpers for the star overlay on the FITS viewer: where a
 * star lands on screen, which stars are in view, which of them get a text
 * label (the most important first, with a cap that grows as you zoom in), and
 * which star the pointer is over.
 */
import { AstrometryOverlayStar } from '../../services/astronomyService';

/** How many stars get a text label at the fitted view. The cap grows as you zoom in. */
export const STAR_LABEL_BUDGET = 12;

/** Screen pixels per image pixel at which the label cap starts to grow (about a fitted large image). */
const LABEL_CAP_REFERENCE_SCALE = 0.25;

/** Screen pixels per image pixel from which the label cap is lifted; only overlap limits the labels. */
const LABEL_CAP_LIFTED_SCALE = 4;

/** Below this many screen pixels per image pixel, unlabeled named stars get no marker. */
export const MARKER_ALL_STARS_SCALE = 0.5;

/** Terms of a star name that mark a Bayer or Flamsteed designation (Greek letters, as the catalogs write them). */
const BAYER_PATTERN = /^(?:alf|bet|gam|del|eps|zet|eta|tet|iot|kap|lam|mu\.?|nu\.?|ksi|omi|pi\.?|rho|sig|tau|ups|phi|chi|psi|ome|alpha|beta|gamma|delta|epsilon|zeta|theta|iota|kappa|lambda|xi|omicron|sigma|upsilon|omega)\b/i;

/** A Flamsteed number with its constellation, such as "61 Cyg". */
const FLAMSTEED_PATTERN = /^\d{1,3}\s+[A-Z][a-z]{2}\b/;

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
 * Ranks how important a star's name is: lower is more important.
 *
 * 0 is a proper name, 1 a Bayer or Flamsteed designation, 2 an HD or HIP number,
 * 3 a Tycho (TYC) number and 4 any other catalog name.
 *
 * @param name The star's name as the backend gave it.
 * @returns The rank, 0 to 4.
 */
export function starNameRank(name: string): number {
  const text = (name ?? '').replace(/^\*\s*/, '').trim();
  if (/^(?:HD|HIP)\b/i.test(text)) return 2;
  if (/^TYC\b/i.test(text)) return 3;
  if (BAYER_PATTERN.test(text) || FLAMSTEED_PATTERN.test(text)) return 1;
  // A name with no digit is a proper name (Vega, Albireo); any other catalog name has a number.
  return text && !/\d/.test(text) ? 0 : 4;
}

/**
 * Gives the most labels allowed at a zoom.
 *
 * Few at the fitted view, more as you zoom in, and no cap once the stars are
 * far apart on screen (overlap alone limits the labels then).
 *
 * @param screenScale Screen pixels per image pixel.
 * @returns The label cap.
 */
export function labelCapForScale(screenScale: number): number {
  if (screenScale >= LABEL_CAP_LIFTED_SCALE) return Number.POSITIVE_INFINITY;
  const zoomedIn = Math.max(1, screenScale / LABEL_CAP_REFERENCE_SCALE);
  return Math.round(STAR_LABEL_BUDGET * zoomedIn ** 1.5);
}

/**
 * Chooses which stars get a text label.
 *
 * Only stars inside the viewport are considered. They are ranked by the
 * importance of their names (proper names, Bayer and Flamsteed, HD and HIP,
 * TYC, then the rest), then by catalog brightness (`gMag`, brighter first),
 * then in the order given. The cap grows with the zoom. A star whose label
 * would cover an already chosen label is skipped. The selected star, when in
 * view, is always labeled first.
 *
 * @param stars All overlay stars, most trustworthy first.
 * @param transform The current image-to-screen mapping.
 * @param estimateLabelWidth Gives a star's label width in screen pixels.
 * @param budget The most labels to choose; the zoom's cap when omitted.
 * @param isSelected Tells whether a star is the selected one.
 * @returns The ids of the stars to label.
 */
export function chooseLabeledStarIds(
  stars: readonly AstrometryOverlayStar[],
  transform: StarScreenTransform,
  estimateLabelWidth: (star: AstrometryOverlayStar) => number,
  budget: number = labelCapForScale(Math.min(transform.scaleX, transform.scaleY)),
  isSelected: (star: AstrometryOverlayStar) => boolean = () => false
): Set<string> {
  const chosen = new Set<string>();
  const boxes: Array<{ left: number; right: number; top: number; bottom: number }> = [];
  const candidates: Array<{ star: AstrometryOverlayStar; rank: number; magnitude: number; index: number; selected: boolean }> = [];
  stars.forEach((star, index) => {
    if (!isInView(starScreenPosition(star, transform), transform)) return;
    candidates.push({
      star, index, selected: isSelected(star), rank: starNameRank(star.name),
      magnitude: typeof star.gMag === 'number' ? star.gMag : Number.POSITIVE_INFINITY
    });
  });
  candidates.sort((a, b) => Number(b.selected) - Number(a.selected)
    || a.rank - b.rank || a.magnitude - b.magnitude || a.index - b.index);
  for (const { star } of candidates) {
    if (chosen.size >= budget) break;
    const position = starScreenPosition(star, transform);
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
