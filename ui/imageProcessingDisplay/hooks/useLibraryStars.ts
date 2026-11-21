/**
 * @file useLibraryStars.ts
 * @description Marks which stars of the astrometry overlay have a record in the library. It asks the
 * backend once for all of the overlay's catalog keys (see `catalogKeysOfStar`) and sets `libraryId`
 * on each star the library has. The answer is kept for the image, so it is asked again only when
 * the stars change.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchExistingStarIds, type AstrometryOverlayStar } from '../../common/services/astronomyService';
import { catalogKeysOfStar } from '../../common/fitsViewer/utils/starNames';

/**
 * Adds `libraryId` to the overlay stars the library has a record of.
 *
 * @param stars The overlay stars of the image on screen.
 * @param imageKey What names the image (target and file), so the answer is kept per image.
 * @returns The same stars, with `libraryId` set on those the library knows. Until the library has
 *   answered, no star has one.
 */
export function useLibraryStars(stars: AstrometryOverlayStar[], imageKey: string): AstrometryOverlayStar[] {
  const keys = useMemo(() => {
    const unique = new Set<string>();
    for (const star of stars) for (const key of catalogKeysOfStar(star)) unique.add(key);
    return [...unique];
  }, [stars]);

  const query = useQuery({
    queryKey: ['libraryStarKeys', imageKey, stars.length, keys.length],
    queryFn: () => fetchExistingStarIds(keys),
    enabled: keys.length > 0,
    staleTime: 60_000,
  });

  return useMemo(() => {
    if (!query.data) return stars;
    const known = new Set(query.data);
    return stars.map((star) => {
      const libraryId = catalogKeysOfStar(star).find((key) => known.has(key)) ?? null;
      return libraryId ? { ...star, libraryId } : star;
    });
  }, [stars, query.data]);
}
