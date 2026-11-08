import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAstronomyListQuery } from '../../common/queries/useAstronomyListQuery';
import { useAstronomyCountQuery } from '../../common/queries/useAstronomyCountQuery';
import { reportError } from '../../common/utils/reportError';
import { SelectableItem } from '../../common/components/SelectableList';
import { Spectrum } from '../../common/types/backendTypes';
import { buildStarListSubtitle, formatStarListLabel } from '../../common/utils/starDisplayFormat';

const STARS_PER_PAGE = 100;

/**
 * Chooses the star to show when nothing has been picked.
 *
 * The backend sorts a target's stars most useful first, so the order is kept
 * within each group: a star with a spectrum wins, then one with photometry
 * (a light curve), then the first star in the list.
 *
 * @param items The star list rows, in the backend's order.
 * @returns The id of the star to select, or undefined when the list is empty.
 */
export const chooseDefaultStar = (items: SelectableItem[]): string | undefined => {
    const best =
        items.find((item) => item.hasSpectra) ??
        items.find((item) => item.hasPhotometry) ??
        items[0];
    return best?.id;
};

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
 * @param setPendingId Setter invoked to choose the default star once the list loads.
 *
 * When a target is in scope and its list first loads (on first open, or after
 * the target changes), a star is chosen so the views are not left empty:
 * the star the user last picked in that target during this session, else the
 * most useful one (see `chooseDefaultStar`). A star already selected when the
 * panel first opens (a hand-off from another view) is kept.
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
        limit: STARS_PER_PAGE,
        offset: (page - 1) * STARS_PER_PAGE,
    }), [targetId, debouncedFilterText, page]);

    const astronomyListQuery = useAstronomyListQuery(queryOptions);
    const spectra = useMemo(() => astronomyListQuery.data ?? [], [astronomyListQuery.data]);

    // Total match count for this scope, used to show "Page X / Y" instead of
    // just "Page X" -- a separate, unpaginated RPC since the list query
    // itself only ever sees one capped page of results.
    const totalCount = useAstronomyCountQuery(targetId, debouncedFilterText).data;
    const totalPages = totalCount !== undefined
        ? Math.max(1, Math.ceil(totalCount / STARS_PER_PAGE))
        : undefined;

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

    useEffect(() => {
        if (!astronomyListQuery.error) return;
        reportError(astronomyListQuery.error, 'astronomy-list');
    }, [astronomyListQuery.error]);

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
            const magnitude = typeof s === 'string' ? undefined : Number(s.magnitude);
            const spectralType = typeof s === 'string' || typeof s.spectralType !== 'string' ? '' : s.spectralType.trim();

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
                magnitude: typeof s !== 'string' && s.magnitude != null && Number.isFinite(magnitude) ? magnitude : undefined,
                hasCatalogMagnitude: typeof s === 'string' ? undefined : (s.hasCatalogMagnitude ?? undefined),
                spectralType: spectralType !== '' && spectralType !== 'Unknown' ? spectralType : undefined,
            };
        });

    const hasMore = spectra.length >= 100;

    // The star the user picked by hand, per target, for this session.
    const pickedStarByTarget = useRef<Map<string, string>>(new Map());
    // The target whose default star has already been chosen.
    const defaultedTargetRef = useRef<string | undefined>(undefined);

    /** Records a star the user picked in the current target, then selects it. */
    const selectStar = useCallback((starId: string) => {
        if (targetId && starId) pickedStarByTarget.current.set(targetId, starId);
        setPendingId?.(starId);
    }, [targetId, setPendingId]);

    // Choose the default star once, when a target's first page of stars has loaded.
    useEffect(() => {
        if (!targetId || !setPendingId || astronomyListQuery.isLoading || !astronomyListQuery.data) return;
        if (debouncedFilterText !== '' || page !== 1 || defaultedTargetRef.current === targetId) return;
        if (filteredItems.length === 0) return;
        const isFirstTarget = defaultedTargetRef.current === undefined;
        defaultedTargetRef.current = targetId;
        // A star already open on first mount (a hand-off) is the user's choice.
        if (isFirstTarget && (pendingId || selectedId)) return;
        const starId = pickedStarByTarget.current.get(targetId) ?? chooseDefaultStar(filteredItems);
        if (starId) setPendingId(starId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [targetId, astronomyListQuery.isLoading, astronomyListQuery.data, debouncedFilterText, page]);

    return {
        items: filteredItems,
        filterText,
        setFilterText: handleSetFilterText,
        page,
        setPage,
        selectStar,
        hasMore,
        totalPages,
        isLoading: astronomyListQuery.isLoading,
        isError: astronomyListQuery.isError,
        retry: () => { void astronomyListQuery.refetch(); },
        hasPrevious: page > 1,
        nextPage: () => setPage((p) => p + 1),
        prevPage: () => setPage((p) => Math.max(1, p - 1)),
    };
};
