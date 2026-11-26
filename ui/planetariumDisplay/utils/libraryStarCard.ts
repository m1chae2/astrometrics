/**
 * @module libraryStarCard
 * @fileoverview Fills in a star handed to the Planetarium (from the Astronomy
 * Manager's "Locate in Planetarium") from its library record, so its card
 * shows the same name, magnitude and spectral type as a pick from the star
 * list.
 */

import { callBackend } from '../../common/services/backendApi';
import { PlanetariumSource } from '../../common/types/planetariumTypes';
import { formatStarListLabel } from '../../common/utils/starDisplayFormat';

/** The fields of a library summary row this reads. */
interface LibraryStarRow {
  id?: string;
  name?: string;
  ra?: number | null;
  dec?: number | null;
  magnitude?: number | null;
  hasCatalogMagnitude?: boolean;
  spectralType?: string;
  hasSpectra?: boolean;
  hasPhotometry?: boolean;
}

/**
 * Looks the star up in the library by its id and returns it with the card
 * fields a star-list row carries. A star the library does not hold, or a
 * failed lookup, gives back the star as it was handed over.
 *
 * @param {PlanetariumSource} source The star as handed over (id, name, position and data flags).
 * @param {AbortSignal} [signal] Cancels the lookup.
 * @returns {Promise<PlanetariumSource>} The star with its library name, magnitude and spectral type.
 */
export async function completeStarFromLibrary(source: PlanetariumSource, signal?: AbortSignal): Promise<PlanetariumSource> {
  try {
    const rows = await callBackend(
      'astronomy:list',
      { search: source.id, limit: 20 },
      { signal, silent: true },
    ) as unknown as LibraryStarRow[] | null;
    const row = (rows ?? []).find((candidate) => candidate.id === source.id);
    if (!row) return source;
    return {
      ...source,
      name: formatStarListLabel(row.name || row.id || source.name),
      ra: typeof row.ra === 'number' ? row.ra : source.ra,
      dec: typeof row.dec === 'number' ? row.dec : source.dec,
      magnitude: row.magnitude ?? undefined,
      hasCatalogMagnitude: row.hasCatalogMagnitude,
      spectralType: row.spectralType || undefined,
      hasSpectra: !!row.hasSpectra,
      hasPhotometry: !!row.hasPhotometry,
      type: 'star',
    };
  } catch {
    // The card then shows what the hand-off carried; the lookup is only an improvement.
    return source;
  }
}
