/**
 * @file usePackageLibrary.ts
 * @description Data hooks for the observation package library: `usePackageLibrary` reads the three
 * lists (Suggested, Planned, Complete) and `usePackageActions` creates, edits and deletes packages.
 *
 * Backend calls: `plan:list_packages` (one call per status, in the order the library returns),
 * `plan:create_package` (a target name; the backend creates a missing catalog target and refuses an
 * unknown one) and `plan:edit_package` (with `expected_revision`, so a stale edit is refused).
 * The `packages:changed` socket event invalidates the library query (handler in ui/App.tsx).
 * Refusals are returned as text for the panel to show; they never become empty lists.
 */
import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { PACKAGE_LIBRARY_QUERY_KEY } from '../../common/queries/packageLibraryQueryKeys';
import { ObservationPackage } from '../../common/types/backendTypes';

export { PACKAGE_LIBRARY_QUERY_KEY };

/** The three lists of the package library, each in the order the library returned it. */
export interface PackageLibraryLists {
    suggested: ObservationPackage[];
    planned: ObservationPackage[];
    complete: ObservationPackage[];
}

/**
 * Reads the three package lists from the backend.
 * @return The lists.
 */
export async function fetchPackageLibrary(): Promise<PackageLibraryLists> {
    const [suggested, planned, complete] = await Promise.all(
        (['SUGGESTED', 'PLANNED', 'COMPLETE'] as const).map(
            (status) => callBackend('plan:list_packages', { status }, { silent: true })
        )
    );
    return { suggested, planned, complete };
}

/**
 * Loads the package library.
 * @return The lists, the loading and error state, and a retry function.
 */
export const usePackageLibrary = () => {
    const query = useQuery({ queryKey: PACKAGE_LIBRARY_QUERY_KEY, queryFn: fetchPackageLibrary });
    return {
        lists: query.data ?? { suggested: [], planned: [], complete: [] },
        isLoading: query.isLoading,
        error: query.error ? (query.error as Error).message || 'Unknown error' : null,
        retry: () => { void query.refetch(); },
    };
};

/** The fields of a package edit that the editor sends. */
export interface PackageEditFields {
    exposure_requests?: Array<Record<string, any>>;
    dither_config?: Record<string, any>;
}

/**
 * Creates, edits and deletes packages. A refusal (unknown target, revision conflict) is kept as text in
 * `actionError` until dismissed.
 * @return The three actions and the refusal text.
 */
export const usePackageActions = () => {
    const queryClient = useQueryClient();
    const [actionError, setActionError] = useState<string | null>(null);

    /** Creates a package for a target. Resolves to the package, or null when refused. */
    const createPackage = useCallback(async (target: string): Promise<ObservationPackage | null> => {
        setActionError(null);
        try {
            const created = await callBackend('plan:create_package', { target }, { silent: true });
            void queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
            return created;
        } catch (error) {
            setActionError(`Could not create a package for ${target}: ${(error as Error).message}`);
            return null;
        }
    }, [queryClient]);

    /** Saves an edit with the revision the editor last saw. Resolves to the saved package, or null when refused. */
    const editPackage = useCallback(async (
        packageId: string,
        fields: PackageEditFields,
        expectedRevision: number | undefined
    ): Promise<ObservationPackage | null> => {
        setActionError(null);
        try {
            const saved = await callBackend(
                'plan:edit_package',
                { package_id: packageId, ...fields, expected_revision: expectedRevision },
                { silent: true }
            );
            void queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
            return saved;
        } catch (error) {
            setActionError(`Could not save the package: ${(error as Error).message}`);
            return null;
        }
    }, [queryClient]);

    /** Deletes a package with the revision the editor last saw. Resolves to true, or false when refused. */
    const deletePackage = useCallback(async (
        packageId: string,
        expectedRevision: number | undefined
    ): Promise<boolean> => {
        setActionError(null);
        try {
            await callBackend(
                'plan:edit_package',
                { package_id: packageId, delete: true, expected_revision: expectedRevision },
                { silent: true }
            );
            void queryClient.invalidateQueries({ queryKey: PACKAGE_LIBRARY_QUERY_KEY });
            return true;
        } catch (error) {
            setActionError(`Could not delete the package: ${(error as Error).message}`);
            return false;
        }
    }, [queryClient]);

    return { createPackage, editPackage, deletePackage, actionError, dismissActionError: () => setActionError(null) };
};
