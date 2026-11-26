/**
 * @file usePhotometryStars.ts
 * @description Keeps only the stack's named stars that have photometry (a light curve), so every star the
 * Astrometry overlay draws on a stack opens in the Astronomy Manager with a light curve. It asks the
 * backend once for the ids of the target's stars with photometry and matches them with the same catalog
 * keys `useLibraryStars` uses (see `catalogKeysOfStar`). It also writes the count line for the viewer.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchPhotometryStarIds, type AstrometryOverlayStar } from '../../common/services/astronomyService';
import { catalogKeysOfStar } from '../../common/fitsViewer/utils/starNames';

/** The note shown when the target has no star with photometry yet. */
export const NO_PHOTOMETRY_NOTE = 'No stars with photometry yet: run Analyze Target';

/** What the overlay shows on a stack. */
export interface PhotometryStarsView {
  /** The named stars that have photometry, each with `libraryId` set to its library record. */
  stars: AstrometryOverlayStar[];
  /** One line saying how many stars are shown, or the no-photometry note. Null until known. */
  countText: string | null;
  /** True while the list of stars with photometry is being read. */
  isLoading: boolean;
}

/**
 * Writes the count line for the stars shown.
 *
 * @param shownCount How many stars with photometry are drawn.
 * @param photometryCount How many of the target's stars have photometry.
 * @returns The line for the viewer.
 */
export function describePhotometryStars(shownCount: number, photometryCount: number): string {
  if (photometryCount === 0) return NO_PHOTOMETRY_NOTE;
  return `Showing ${shownCount.toLocaleString('en-US')} ${shownCount === 1 ? 'star' : 'stars'} with photometry`;
}

/**
 * Keeps the stack's stars that have photometry.
 *
 * @param stars The stack's named stars.
 * @param targetId The selected target, or null.
 * @param enabled True while a stack is shown with the Astrometry overlay on.
 * @returns The stars to draw and the count line.
 */
export function usePhotometryStars(
  stars: AstrometryOverlayStar[],
  targetId: string | null,
  enabled: boolean,
): PhotometryStarsView {
  const query = useQuery({
    queryKey: ['photometryStarIds', targetId],
    queryFn: () => fetchPhotometryStarIds(targetId as string),
    enabled: Boolean(targetId) && enabled,
    staleTime: 30_000,
  });

  return useMemo(() => {
    if (!enabled) return { stars: [], countText: null, isLoading: false };
    if (!query.data) return { stars: [], countText: null, isLoading: query.isFetching || query.isPending };
    const withPhotometry = new Set(query.data);
    const shown: AstrometryOverlayStar[] = [];
    for (const star of stars) {
      const libraryId = catalogKeysOfStar(star).find((key) => withPhotometry.has(key));
      if (libraryId) shown.push({ ...star, libraryId });
    }
    // Before the stack's names are read there is nothing to count yet.
    const countText = stars.length > 0 || withPhotometry.size === 0
      ? describePhotometryStars(shown.length, withPhotometry.size)
      : null;
    return { stars: shown, countText, isLoading: false };
  }, [enabled, stars, query.data, query.isFetching, query.isPending]);
}
