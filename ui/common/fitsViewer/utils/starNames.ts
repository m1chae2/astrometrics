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

/** The start of a name that is only a Gaia source number, which the Astronomy Manager has no record for. */
const GAIA_ONLY_NAME_PREFIX = 'Gaia DR3 ';

/**
 * Says whether the Astronomy Manager can open an overlay star.
 *
 * A star can be opened when the library has a record for it (its `libraryId`, set once the library
 * was asked). Gaia DR3-named records count. A star with no record has nothing to show there.
 *
 * @param star The overlay star.
 * @returns True when opening the star in the Astronomy Manager can show it.
 */
export function canOpenInAstronomyManager(star: { libraryId?: string | null }): boolean {
  return Boolean(star.libraryId);
}

/** A stack's own id for a star ("detection-5", "gaia-123"), which is not a library id. */
const STACK_STAR_ID = /^(detection|gaia)-\d+$/;

/**
 * Lists the keys under which the library may hold an overlay star, best first.
 *
 * The library saves a Gaia-matched star under "Gaia DR3 <source id>" and any other catalog star under
 * its catalog name. A star read from the library carries its record id. A stack's own id
 * ("detection-5") is not a library key and is never listed.
 *
 * @param star The overlay star.
 * @returns The Gaia DR3 id (when known), the catalog name (when it is a real one), then the star's own id.
 */
export function catalogKeysOfStar(star: Pick<OverlayStar, 'name'> & { id?: string; gaiaSourceId?: string | null }): string[] {
  const keys: string[] = [];
  if (star.gaiaSourceId) keys.push(`${GAIA_ONLY_NAME_PREFIX}${star.gaiaSourceId}`);
  const name = (star.name || '').trim();
  if (name && name !== UNNAMED_STAR && !keys.includes(name)) keys.push(name);
  const ownId = (star.id || '').trim();
  if (ownId && !STACK_STAR_ID.test(ownId) && !keys.includes(ownId)) keys.push(ownId);
  return keys;
}

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
