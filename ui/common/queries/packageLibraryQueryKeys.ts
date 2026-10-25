/**
 * @file packageLibraryQueryKeys.ts
 * @description React Query key of the observation package library, shared by the Observation
 * Manager's data hook and the socket handler in App.tsx that refreshes it on `packages:changed`.
 */

/** Key of the package library lists (suggested, planned, complete). */
export const PACKAGE_LIBRARY_QUERY_KEY = ['observationPackages'] as const;

/** Key of tonight's observation session with its progress (`plan:current_session`). */
export const CURRENT_SESSION_QUERY_KEY = ['observationCurrentSession'] as const;

/**
 * Key that holds the newest suggestion refresh result. App.tsx writes it when a `packages:changed`
 * event of kind `refreshed` arrives; the package library reads it to show "Suggestions updated: ...".
 */
export const PACKAGE_REFRESH_NOTICE_QUERY_KEY = ['observationPackageRefreshNotice'] as const;

/** What a `packages:changed` event of kind `refreshed` reports: how many packages the refresh changed. */
export interface PackageRefreshCounts {
    added?: number;
    updated?: number;
    deleted?: number;
    completed?: number;
    reverted?: number;
}
