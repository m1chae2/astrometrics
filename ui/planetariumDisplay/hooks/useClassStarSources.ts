/**
 * @module useClassStarSources
 * @fileoverview The stars the Planetarium draws in their own colors while
 * the left panel browses by spectral class.
 */

import { useMemo } from 'react';
import { useClassStarColorsQuery } from '../../common/queries/useClassStarColorsQuery';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { spectralClassLetter } from '../../common/utils/starDisplayFormat';
import { starColorClass } from '../utils/starColor';

/**
 * Most stars a chosen spectral class may have to be drawn whole at any zoom. Larger classes,
 * and "All", follow the zoom rule (fainter stars appear as the view narrows). Chosen so a rare
 * class (C, W, O: tens of stars) always shows completely, while the big classes (thousands)
 * do not crowd a wide view.
 */
export const ALWAYS_SHOW_CLASS_MAX_STARS = 200;

/** Return shape of useClassStarSources. */
export interface ClassStarSourcesResult {
  /** The stars to draw, each with its color class. Empty while the mode is off. */
  sources: PlanetariumSource[];
  /** How many stars there are before the class filter. */
  totalCount: number;
  /** Whether the stars are still loading. */
  isLoading: boolean;
  /** Whether the stars could not be loaded. */
  isError: boolean;
  /** Asks for the stars again after a failure. */
  retry: () => void;
}

/**
 * Loads every star of the spectral class lists, and every star with a
 * spectrum, whatever target is chosen, and turns each into a sky map source
 * with its color class.
 *
 * A star's color comes from our own spectrum's type, else the catalog type,
 * else the catalog B-V color (see `starColorClass`). When a class is chosen
 * in the left list, only the stars of that catalog class are kept: the same
 * stars that class's star list shows.
 *
 * @func useClassStarSources
 * @param {boolean} enabled Whether the spectral class mode is on.
 * @param {string | undefined} spectralClass The chosen class letter, or undefined for all classes.
 * @returns {ClassStarSourcesResult} The stars to draw and the loading state.
 */
export const useClassStarSources = (
  enabled: boolean,
  spectralClass: string | undefined,
): ClassStarSourcesResult => {
  const query = useClassStarColorsQuery(enabled);
  const rows = useMemo(() => query.data ?? [], [query.data]);

  const allSources = useMemo<PlanetariumSource[]>(
    () =>
      rows
        .filter((row) => typeof row.ra === 'number' && typeof row.dec === 'number')
        .map((row) => ({
          id: row.id,
          ra: row.ra as number,
          dec: row.dec as number,
          name: row.name || row.id,
          spectralType: row.spectralType || undefined,
          magnitude: row.magnitude ?? undefined,
          hasCatalogMagnitude: row.hasCatalogMagnitude,
          hasSpectra: !!row.hasSpectra,
          hasPhotometry: !!row.hasPhotometry,
          type: 'star',
          colorClass: starColorClass(row),
        })),
    [rows],
  );

  const sources = useMemo(() => {
    if (!enabled) return [];
    if (!spectralClass) return allSources;
    const inClass = allSources.filter((source) => spectralClassLetter(source.spectralType) === spectralClass);
    // A small chosen class is drawn whole, whatever the zoom, so none of its few stars is
    // missed; a big one follows the zoom rule like every other star.
    return inClass.length <= ALWAYS_SHOW_CLASS_MAX_STARS
      ? inClass.map((source) => ({ ...source, alwaysShow: true }))
      : inClass;
  }, [enabled, spectralClass, allSources]);

  return {
    sources,
    totalCount: allSources.length,
    isLoading: enabled && query.isLoading,
    isError: enabled && query.isError,
    retry: () => { void query.refetch(); },
  };
};
