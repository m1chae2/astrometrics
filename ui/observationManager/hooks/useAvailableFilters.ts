/**
 * @file useAvailableFilters.ts
 * @description Reads the filters this observatory's filter wheel is loaded with (`system:filters`,
 * the `[Observatory.Filters] available` setting). The library returns each filter's value, such as
 * "Luminance" or "Star Analyzer 200"; the Image Planner shows them by their short names.
 */
import { useBackendFetch } from '../../common/hooks/useBackendFetch';
import { callBackend } from '../../common/services/backendApi';

/** What {@link useAvailableFilters} returns. */
export interface AvailableFilters {
    /** The configured filters as the library names them, in the configured order. Empty before the read. */
    filters: string[];
    /** Whether the first read is still running. */
    loading: boolean;
    /** The error text of a failed read, or null. */
    error: string | null;
}

/**
 * Reads the observatory's configured filters once.
 * @return The filters, the loading flag and any error.
 */
export function useAvailableFilters(): AvailableFilters {
    const { data, loading, error } = useBackendFetch<string[]>(
        (signal) => callBackend('system:filters', {}, { signal }),
        [],
        { errorMessage: 'Could not read the configured filters' },
    );
    return { filters: Array.isArray(data) ? data : [], loading, error };
}
