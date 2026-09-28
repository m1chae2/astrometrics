import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAstronomyListQuery } from '../../common/queries/useAstronomyListQuery';
import { useToast } from '../../common/hooks/useToast';
import { SelectableItem } from '../../common/components/SelectableList';
import { Spectrum } from '../../common/types/backendTypes';
import { buildStarListSubtitle, formatStarListLabel } from '../utils/starDisplayFormat';

/**
 * Fetches and paginates the star list for one scope -- a single target, or
 * the whole catalog when `targetId` is undefined -- with text search.
 *
 * Owns only the star list itself: which target (or spectral class) is in
 * scope is decided by the caller (see `useTargetBrowserItems` and
 * `useSpectralClassBrowserItems`), not by this hook.
 *
 * @param targetId The target to scope the list to, or undefined for the whole catalog.
 * @param reloadKey Changing this value re-triggers the star list fetch.
 * @param pendingId ID of a star awaiting confirmation of selection.
 * @param selectedId ID of the currently selected star, if any.
 * @param setPendingId Setter invoked to auto-select the first star once loaded.
 */
export const useSpectrumList = (
    targetId?: string,
    reloadKey?: number,
    pendingId?: string,
    selectedId?: string,
    setPendingId?: (t: string) => void
) => {
    const [filterText, setFilterText] = useState<string>('');
    const [debouncedFilterText, setDebouncedFilterText] = useState<string>('');
    const [page, setPage] = useState<number>(1);
    const toast = useToast();

    // Reset page to 1 when search text changes
    const handleSetFilterText = useCallback((text: string) => {
        setFilterText(text);
        setPage(1);
    }, []);

    // Debounce search text input by 250ms to prevent excessive backend queries
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedFilterText(filterText);
        }, 250);
        return () => clearTimeout(timer);
    }, [filterText]);

    // Reset to page 1 whenever the scope itself changes.
    useEffect(() => {
        setPage(1);
    }, [targetId]);

    // Fetch astronomy list capped at 100 stars with full database search and offset pagination
    const queryOptions = useMemo(() => ({
        targetId,
        search: debouncedFilterText,
        limit: 100,
        offset: (page - 1) * 100,
    }), [targetId, debouncedFilterText, page]);

    const astronomyListQuery = useAstronomyListQuery(queryOptions);
    const spectra = useMemo(() => astronomyListQuery.data ?? [], [astronomyListQuery.data]);

    // Callers change reloadKey to force a refresh.
    const isFirstReloadKeyRender = useRef(true);
    useEffect(() => {
        if (isFirstReloadKeyRender.current) {
            isFirstReloadKeyRender.current = false;
            return;
        }
        astronomyListQuery.refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reloadKey]);

    // Auto-select the first spectrum once loaded, if nothing is selected/pending yet
    useEffect(() => {
        if (spectra.length > 0 && !pendingId && !selectedId && setPendingId) {
            const first = spectra[0];
            setPendingId(String(first.id ?? first.label ?? first));
        }
    }, [spectra, selectedId, pendingId, setPendingId]);

    useEffect(() => {
        if (!astronomyListQuery.error) return;
        console.error('Failed to fetch astronomy list', astronomyListQuery.error);
        try {
            toast.show(
                astronomyListQuery.error instanceof Error
                    ? astronomyListQuery.error.message
                    : String(astronomyListQuery.error),
                'error'
            );
        } catch {
            // Ignore
        }
    }, [astronomyListQuery.error, toast]);

    const applyTextFilter = useCallback(
        (s: Spectrum) => {
            if (!filterText || filterText.trim() === '') return true;
            const needle = filterText.trim().toLowerCase();
            const val = typeof s === 'string' ? s : `${s.name || ''} ${s.label || ''} ${s.id || ''}`;
            return val.toLowerCase().includes(needle);
        },
        [filterText]
    );

    const checkHasSpectra = useCallback((s: Spectrum): boolean => {
        if (typeof s === 'string') return false;
        return (
            !!s.hasSpectra ||
            !!s.has_spectra ||
            !!(s.spectroscopy && s.spectroscopy.wavelengthsAngstrom && s.spectroscopy.wavelengthsAngstrom.length > 0)
        );
    }, []);

    const checkHasPhotometry = useCallback((s: Spectrum): boolean => {
        if (typeof s === 'string') return false;
        return (
            !!s.hasPhotometry ||
            !!s.has_photometry ||
            (!!s.photometry &&
                ((Array.isArray(s.photometry.timestamps) && s.photometry.timestamps.length > 0) ||
                 (Array.isArray(s.photometry.magnitudes) && s.photometry.magnitudes.length > 0) ||
                 (Array.isArray(s.photometry.fluxes) && s.photometry.fluxes.length > 0)))
        );
    }, []);

    const filteredItems: SelectableItem[] = spectra
        .filter((s) => {
            const name = typeof s === 'string' ? s : (s.name || s.id || s.label || '');
            const idStr = typeof s === 'string' ? s : (s.id || s.name || s.label || '');
            if (name.startsWith('Star_') || name.startsWith('Star ') || idStr.startsWith('Star_') || idStr.startsWith('Star ')) {
                return false;
            }
            return true;
        })
        .filter((s) => applyTextFilter(s))
        .slice(0, 100)
        .map((s) => {
            const objectId = typeof s === 'string' ? s : (s.id || s.name || s.label || '');
            const value = objectId;
            const fullName = typeof s === 'string' ? s : (s.name || s.label || s.id || '');
            const hasSpectra = checkHasSpectra(s);
            const hasPhotometry = checkHasPhotometry(s);

            // The list column is narrow, so the label is shortened to keep the
            // part that tells stars apart; the full name and id are the hover text.
            const tooltip = fullName === value ? fullName : `${fullName} (${value})`;
            const ra = typeof s === 'string' ? undefined : Number(s.ra);
            const dec = typeof s === 'string' ? undefined : Number(s.dec);

            return {
                id: value,
                value: value,
                label: formatStarListLabel(fullName),
                subtitle: typeof s === 'string' ? '' : buildStarListSubtitle(s),
                tooltip,
                hasSpectra,
                hasPhotometry,
                ra: Number.isFinite(ra) ? ra : undefined,
                dec: Number.isFinite(dec) ? dec : undefined,
            };
        });

    const hasMore = spectra.length >= 100;

    return {
        items: filteredItems,
        filterText,
        setFilterText: handleSetFilterText,
        page,
        setPage,
        hasMore,
        hasPrevious: page > 1,
        nextPage: () => setPage((p) => p + 1),
        prevPage: () => setPage((p) => Math.max(1, p - 1)),
    };
};
