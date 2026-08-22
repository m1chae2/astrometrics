/**
 * @module catalogSourceCache
 * @fileoverview In-memory, containment-aware cache for online catalog source queries.
 *
 * Panning and zooming re-derive (ra, dec, radius) on every change, so without a
 * cache, re-visiting a region you already queried a moment ago (e.g. zooming in
 * then back out) always waits on a fresh network/RPC round trip even though the
 * answer hasn't changed. This cache remembers recent query results and serves a
 * new query instantly, filtered client-side, whenever an existing cached query's
 * circle fully contains the new one — no backend call needed.
 */

import { PlanetariumSource } from '../../common/types/planetariumTypes';

interface CacheEntry {
  ra: number;
  dec: number;
  radius: number;
  driversKey: string;
  timestamp: number;
  sources: PlanetariumSource[];
}

/** Entries older than this are treated as expired and ignored for cache hits. */
const CACHE_TTL_MS = 5 * 60 * 1000;

/** Caps memory use; oldest entries are evicted first once exceeded. */
const MAX_ENTRIES = 50;

const cache: CacheEntry[] = [];

/**
 * Great-circle separation between two RA/Dec points, in degrees (haversine).
 */
function angularSeparationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const ra1Rad = (ra1 * Math.PI) / 180.0;
  const dec1Rad = (dec1 * Math.PI) / 180.0;
  const ra2Rad = (ra2 * Math.PI) / 180.0;
  const dec2Rad = (dec2 * Math.PI) / 180.0;

  const deltaDec = dec2Rad - dec1Rad;
  const deltaRa = ra2Rad - ra1Rad;
  const haversineTerm =
    Math.sin(deltaDec / 2) ** 2 + Math.cos(dec1Rad) * Math.cos(dec2Rad) * Math.sin(deltaRa / 2) ** 2;
  return (2 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, haversineTerm)))) * 180.0) / Math.PI;
}

/**
 * Returns cached sources for a query if a live, sufficiently large prior query
 * fully covers it, filtered down to the requested radius. Returns null on a
 * cache miss (caller should fetch and then call `storeCatalogSources`).
 *
 * @param {number} ra - Query center Right Ascension in degrees.
 * @param {number} dec - Query center Declination in degrees.
 * @param {number} radius - Query radius in degrees.
 * @param {string} driversKey - Stable key identifying the enabled driver set for this query.
 * @returns {PlanetariumSource[] | null} The cached sources within radius, or null on a cache miss.
 */
export function findCachedCatalogSources(
  ra: number,
  dec: number,
  radius: number,
  driversKey: string,
): PlanetariumSource[] | null {
  const now = Date.now();
  for (let i = cache.length - 1; i >= 0; i--) {
    const entry = cache[i];
    if (entry.driversKey !== driversKey) continue;
    if (now - entry.timestamp > CACHE_TTL_MS) continue;

    const centerSeparation = angularSeparationDeg(ra, dec, entry.ra, entry.dec);
    if (centerSeparation + radius > entry.radius) continue;

    return entry.sources.filter(source => angularSeparationDeg(ra, dec, source.ra, source.dec) <= radius);
  }
  return null;
}

/**
 * Records a completed query's results so future covered queries can reuse them.
 *
 * @param {number} ra - Query center Right Ascension in degrees.
 * @param {number} dec - Query center Declination in degrees.
 * @param {number} radius - Query radius in degrees.
 * @param {string} driversKey - Stable key identifying the enabled driver set for this query.
 * @param {PlanetariumSource[]} sources - The fetched sources to cache for this query.
 * @returns {void}
 */
export function storeCatalogSources(
  ra: number,
  dec: number,
  radius: number,
  driversKey: string,
  sources: PlanetariumSource[],
): void {
  cache.push({ ra, dec, radius, driversKey, timestamp: Date.now(), sources });
  while (cache.length > MAX_ENTRIES) {
    cache.shift();
  }
}
