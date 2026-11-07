/**
 * @fileoverview Tests for the catalog source cache, including results the backend cut short.
 *
 * A result cut short (the Gaia driver stops at 20,000 stars) lacks the faintest stars of its
 * circle, so it must never answer a different query.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  clearCatalogSourceCache,
  findCachedCatalogSources,
  storeCatalogSources,
} from '../planetariumDisplay/utils/catalogSourceCache';

const star = (id: string, ra = 10, dec = 10) => ({
  id, ra, dec, name: id, hasSpectra: false, hasPhotometry: false, type: 'star' as const,
});

describe('catalogSourceCache', () => {
  beforeEach(() => {
    clearCatalogSourceCache();
  });

  it('answers a smaller query inside a complete entry', () => {
    storeCatalogSources(10, 10, 5, 'deep_stars', [star('a')], 14);

    const hit = findCachedCatalogSources(10, 10, 2, 'deep_stars', 14);

    expect(hit?.map(source => source.id)).toEqual(['a']);
  });

  it('does not answer a smaller query from an entry the backend cut short', () => {
    storeCatalogSources(10, 10, 5, 'deep_stars', [star('a')], 14, true);

    expect(findCachedCatalogSources(10, 10, 2, 'deep_stars', 14)).toBeNull();
  });

  it('does not answer even the same query from an entry cut short', () => {
    storeCatalogSources(10, 10, 5, 'deep_stars', [star('a')], 14, true);

    expect(findCachedCatalogSources(10, 10, 5, 'deep_stars', 14)).toBeNull();
  });

  it('skips a cut-short entry but still uses an older complete one', () => {
    storeCatalogSources(10, 10, 8, 'deep_stars', [star('complete')], 14, false);
    storeCatalogSources(10, 10, 8, 'deep_stars', [star('cut')], 14, true);

    const hit = findCachedCatalogSources(10, 10, 2, 'deep_stars', 14);

    expect(hit?.map(source => source.id)).toEqual(['complete']);
  });
});
