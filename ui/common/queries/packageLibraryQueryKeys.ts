/**
 * @file packageLibraryQueryKeys.ts
 * @description React Query key of the observation package library, shared by the Observation
 * Manager's data hook and the socket handler in App.tsx that refreshes it on `packages:changed`.
 */

/** Key of the package library lists (suggested, planned, complete). */
export const PACKAGE_LIBRARY_QUERY_KEY = ['observationPackages'] as const;
