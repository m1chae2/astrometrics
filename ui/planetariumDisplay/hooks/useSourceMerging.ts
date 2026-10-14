/**
 * @module useSourceMerging
 * @fileoverview Queries and merges the three star/target data sources the
 * Planetarium sky map draws from -- the viewport-scoped local catalog, the
 * whole-sky bundled bright-star catalog, and the viewport-scoped deep (Gaia)
 * catalog -- into the single deduplicated `stars`/`targets` arrays CelestialSkyMap
 * consumes.
 *
 * Extracted from PlanetariumDisplay to keep the root component focused on
 * layout/composition rather than catalog-query bookkeeping.
 */

import { useMemo } from 'react';
import { usePlanetariumSources } from './usePlanetariumSources';
import { useOnlineCatalogSources, LOCAL_CATALOG_QUERY_DEBOUNCE_MS } from './useOnlineCatalogSources';
import { usePlanetariumTargets } from './usePlanetariumTargets';
import { useDeepCatalogStatus } from './useDeepCatalogStatus';
import { computeLimitingMagnitude, DEEP_STAR_MAX_MAGNITUDE, UNCATALOGED_STAR_MAX_FOV_DEG } from '../layers/StarOverlay';
import { PlanetariumTarget } from '../../common/types/planetariumTypes';

/** Return shape of useSourceMerging. */
export interface SourceMergingResult {
  /** Star sources for the current viewport, deduplicated across local/bright/deep catalogs. */
  stars: ReturnType<typeof usePlanetariumSources>['sources'];
  /** Target objects for the current viewport, merged with the full local target library. */
  targets: PlanetariumTarget[];
  /** Deep (Gaia) star catalog installation/build status, for the download-prompt UI. */
  deepCatalogStatus: ReturnType<typeof useDeepCatalogStatus>['status'];
  /** Local library targets (used directly by handleSelectObject's lookup priority). */
  libraryTargets: ReturnType<typeof usePlanetariumTargets>['targets'];
}

/**
 * Queries and deduplicates the local, bright-star, and deep-star catalog
 * sources for the current viewport/FOV, and merges queried targets with the
 * full local target library.
 *
 * @func useSourceMerging
 * @param {number} raValue - Viewport center RA in degrees.
 * @param {number} decValue - Viewport center Dec in degrees.
 * @param {number} currentFOV - Current field of view in degrees.
 * @param {boolean} showStars - Whether the star catalog overlay is enabled.
 * @returns {SourceMergingResult} Deduplicated stars/targets and deep-catalog status.
 */
export const useSourceMerging = (
  raValue: number,
  decValue: number,
  currentFOV: number,
  showStars: boolean,
): SourceMergingResult => {
  // Extend query radius 1.5x beyond FOV to preload sources at pan edges; minimum 0.5deg
  const queryRadius = useMemo(() => Math.max(currentFOV * 1.5, 0.5), [currentFOV]);

  // Same cutoff the renderer applies (see isDisplayableStar), so the backend can skip
  // stars that would be fetched and then immediately discarded.
  const limitingMagnitude = useMemo(() => computeLimitingMagnitude(currentFOV), [currentFOV]);
  const includeStarsWithoutCatalogMagnitude = currentFOV <= UNCATALOGED_STAR_MAX_FOV_DEG;

  const { sources: localSources } = usePlanetariumSources(
    raValue,
    decValue,
    queryRadius,
    limitingMagnitude,
    includeStarsWithoutCatalogMagnitude,
  );

  // Whole-sky bright-star query: served from the locally bundled Hipparcos
  // extract ('hipparcos') rather than a live query. GAIA DR3 is unsuitable
  // for this layer -- its detectors saturate on very bright stars, so it's
  // missing nearly every naked-eye-famous star.
  //
  // This driver's backend query is a fast in-memory numpy filter over a
  // bundled extract (no network round trip) and its maximum_query_radius_degrees
  // is 180 -- a true whole sky. So rather than rescoping to the viewport on
  // every pan (which meant re-querying, and a brief empty gap, every time you
  // panned into a not-yet-queried region, including areas hidden behind the
  // ground/horizon overlay that later rotate into view), we query the entire
  // sky once with a fixed center/radius. The backend's brightest-5000 cap
  // still applies, so the overlay is always handed the 5000 brightest stars
  // in the sky, not 83k.
  const brightStarDrivers = useMemo(() => ['hipparcos'], []);
  const { onlineSources: brightStarSources } = useOnlineCatalogSources(
    0, 0, 180,
    brightStarDrivers,
    true,
    // Fixed whole-sky query that never changes with the view, so there is nothing to wait out.
    { debounceMilliseconds: 0 },
  );

  // Faint-star query: the Gaia DR3 copy downloaded to this computer (see the
  // deep-star catalog prompt) supplies stars fainter than the bundled Hipparcos
  // extract, scoped to the current viewport since its per-region density is far
  // higher. A local database read, so it takes milliseconds and never touches the
  // internet. Gated on showStars so toggling the background field off also stops it.
  const deepStarDrivers = useMemo(() => ['deep_stars'], []);
  const { status: deepCatalogStatus } = useDeepCatalogStatus();
  // The depth the catalog was actually built to, as it reports itself; the default only stands
  // in until the status arrives or when no catalog is installed.
  const deepCatalogMagnitudeLimit = deepCatalogStatus?.magnitude_limit ?? DEEP_STAR_MAX_MAGNITUDE;
  // Rounded up to a whole magnitude so a slow zoom reuses one query (and one cached region)
  // instead of asking for a slightly deeper limit at every step, and capped at the depth the
  // catalog holds so a deeper request is not cached as if it had been served.
  const deepStarLimitingMagnitude = useMemo(
    () => Math.min(Math.ceil(limitingMagnitude), deepCatalogMagnitudeLimit),
    [limitingMagnitude, deepCatalogMagnitudeLimit],
  );
  const { onlineSources: deepStarSources } = useOnlineCatalogSources(
    raValue, decValue, queryRadius,
    deepStarDrivers,
    showStars,
    // A local lookup, so only a short wait to skip the steps of one gesture, not the 300 ms a remote query needs.
    { limitingMagnitude: deepStarLimitingMagnitude, debounceMilliseconds: LOCAL_CATALOG_QUERY_DEBOUNCE_MS },
  );

  const sources = useMemo(() => {
    const localIds = new Set(localSources.map(source => source.id));
    const combinedOnline = [
      ...brightStarSources,
      ...deepStarSources.filter(source => !localIds.has(source.id)),
    ];
    const seenOnlineIds = new Set<string>();
    const dedupedOnline = combinedOnline.filter(source => {
      if (localIds.has(source.id) || seenOnlineIds.has(source.id)) return false;
      seenOnlineIds.add(source.id);
      return true;
    });
    return [...localSources, ...dedupedOnline];
  }, [localSources, brightStarSources, deepStarSources]);

  const { targets: libraryTargets } = usePlanetariumTargets();

  // Split sources into stars and targets, ensuring all library targets are included
  const stars = useMemo(() => sources.filter(source => source.type === 'star'), [sources]);
  const targets = useMemo(() => {
    const queriedTargets = sources.filter(source => source.type === 'target') as PlanetariumTarget[];
    const seenIds = new Set(queriedTargets.map(t => t.id));
    const merged = [...queriedTargets];
    for (const lt of libraryTargets) {
      if (!seenIds.has(lt.id)) {
        seenIds.add(lt.id);
        merged.push(lt);
      }
    }
    return merged;
  }, [sources, libraryTargets]);

  return { stars, targets, deepCatalogStatus, libraryTargets };
};
