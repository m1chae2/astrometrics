/**
 * @module catalogVersionStore
 * @fileoverview Counts the backend's `catalog:changed` events, so a screen
 * that keeps its own star data outside the query cache (the Planetarium's
 * sky sources) can read it again when stars change.
 *
 * App.tsx calls `bumpCatalogVersion` on each event. The react-query star
 * lists are refreshed by query invalidation instead.
 */

import { useSyncExternalStore } from 'react';

let catalogVersion = 0;
const listeners = new Set<() => void>();

/**
 * Records that the star catalog changed, and tells every listener.
 *
 * @returns {void}
 */
export function bumpCatalogVersion(): void {
  catalogVersion += 1;
  listeners.forEach((listener) => listener());
}

/**
 * Adds a listener for catalog changes.
 *
 * @param {() => void} listener Called after each change.
 * @returns {() => void} Removes the listener.
 */
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * The number of catalog changes seen so far. It goes up by one on each
 * `catalog:changed` event, so a hook can list it as a dependency.
 *
 * @returns {number} The change count.
 */
export function useCatalogVersion(): number {
  return useSyncExternalStore(subscribe, () => catalogVersion, () => catalogVersion);
}
