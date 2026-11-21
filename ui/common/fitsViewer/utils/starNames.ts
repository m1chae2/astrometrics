/**
 * @file starNames.ts
 * @description Names the star the star profile measured, for the top of the profile panel. When the
 * astrometry overlay holds a star at the measured center, its name is used (the most familiar
 * designation the backend picked). Otherwise, on a plate-solved image, the star is "Unnamed star" with
 * its right ascension and declination. An image with neither gives no name.
 */
import type { OverlayStar } from '../../types/backendTypes';
import type { PixelToSky } from '../wcs';
import { formatDeclination, formatRightAscension } from '../wcs';

/** How far, in image pixels, an overlay star may sit from the measured center and still be the same star. */
export const SAME_STAR_DISTANCE_PX = 3;

/** The name of a star no catalog names. */
export const UNNAMED_STAR = 'Unnamed star';

/** An overlay star, with the kind of match a stack's own star names carry. */
type NamedStar = Pick<OverlayStar, 'x' | 'y' | 'name'> & { matchKind?: string };

/**
 * Finds the overlay star at a measured center.
 *
 * @param centerX Column of the center, 1-based (as the star profile gives it).
 * @param centerY Row of the center, 1-based.
 * @param stars The overlay stars, at 0-based pixels of the same image, or undefined.
 * @returns The nearest star within {@link SAME_STAR_DISTANCE_PX}, or null.
 */
export function findStarAt<T extends Pick<OverlayStar, 'x' | 'y'>>(centerX: number, centerY: number, stars: T[] | undefined): T | null {
  let nearest: T | null = null;
  let nearestDistance = SAME_STAR_DISTANCE_PX;
  for (const star of stars ?? []) {
    const distance = Math.hypot(star.x - (centerX - 1), star.y - (centerY - 1));
    if (distance <= nearestDistance) {
      nearest = star;
      nearestDistance = distance;
    }
  }
  return nearest;
}

/**
 * Names the star at a measured center.
 *
 * @param centerX Column of the center, 1-based (as the star profile gives it).
 * @param centerY Row of the center, 1-based.
 * @param stars The overlay stars, at 0-based pixels of the same image, or undefined.
 * @param pixelToSky The image's plate solution, taking 1-based pixels, or null.
 * @returns The name, "Unnamed star" with the sky position, or null.
 */
export function nameStarAt(
  centerX: number,
  centerY: number,
  stars: NamedStar[] | undefined,
  pixelToSky: PixelToSky | null,
): string | null {
  const nearest = findStarAt(centerX, centerY, stars);
  if (nearest && nearest.matchKind !== 'unmatched' && nearest.name && nearest.name !== UNNAMED_STAR) {
    return nearest.name;
  }
  if (!pixelToSky) return nearest ? UNNAMED_STAR : null;
  const sky = pixelToSky(centerX, centerY);
  return `${UNNAMED_STAR} at ${formatRightAscension(sky.raDegrees)} ${formatDeclination(sky.decDegrees)}`;
}
