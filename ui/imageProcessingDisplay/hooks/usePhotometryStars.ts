/**
 * @file usePhotometryStars.ts
 * @description Keeps only the stack's named stars that have photometry (a light curve), so every star the
 * Astrometry overlay draws on a stack opens in the Astronomy Manager with a light curve. It asks the
 * backend once for the target's stars with photometry and keeps the stars whose library id
 * (`libraryId`, set by the library when it named the stack) is one of them. Each kept star is labelled
 * with its library record's name, so the overlay and the Astronomy Manager's lists show the same name.
 * It also writes the count line for the viewer.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchPhotometryStarNames, type AstrometryOverlayStar } from '../../common/services/astronomyService';

/** The note shown when the target has no star with photometry yet. */
export const NO_PHOTOMETRY_NOTE = 'No stars with photometry yet: run Analyze Target';

/** What the overlay shows on a stack. */
export interface PhotometryStarsView {
  /** The named stars that have photometry. */
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
    queryKey: ['photometryStarNames', targetId],
    queryFn: () => fetchPhotometryStarNames(targetId as string),
    enabled: Boolean(targetId) && enabled,
    staleTime: 30_000,
  });

  return useMemo(() => {
    if (!enabled) return { stars: [], countText: null, isLoading: false };
    if (!query.data) return { stars: [], countText: null, isLoading: query.isFetching || query.isPending };
    const withPhotometry = query.data;
    const shown: AstrometryOverlayStar[] = [];
    for (const star of stars) {
      const recordName = star.libraryId ? withPhotometry.get(star.libraryId) : undefined;
      if (recordName !== undefined) shown.push({ ...star, name: recordName });
    }
    // Before the stack's names are read there is nothing to count yet.
    const countText = stars.length > 0 || withPhotometry.size === 0
      ? describePhotometryStars(shown.length, withPhotometry.size)
      : null;
    return { stars: shown, countText, isLoading: false };
  }, [enabled, stars, query.data, query.isFetching, query.isPending]);
}
