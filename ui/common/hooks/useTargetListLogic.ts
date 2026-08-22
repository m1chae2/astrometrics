import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTargetListQuery } from '../queries/useTargetListQuery';
import { useAstronomyListQuery } from '../queries/useAstronomyListQuery';
import { useToast } from './useToast';
import { SelectableItem } from '../components/SelectableList';

interface Target {
    id?: string;
    name?: string;
    [key: string]: unknown;
}

const BASE_FILTER_OPTIONS = ['Target Objects', 'Messier Objects', 'NGC IC Objects', 'No Image'];

/**
 * Fetches, filters, and manages selection state for the target/star radio list
 * shown in the left-hand panel of the Planetarium and Astronomy displays.
 *
 * Fetches the target list (and, lazily, the star list) from the backend, applies
 * the active dropdown/text/processed-only filters, computes fuzzy-matched
 * highlighted IDs against remote ingestion folders, and auto-selects the first
 * filtered item when nothing is already selected or pending.
 *
 * @param {number | undefined} reloadKey - Changing this value re-triggers the target/star fetch.
 * @param {string | undefined} pendingTarget - ID of a target awaiting confirmation of selection.
 * @param {string | undefined} selectedTarget - ID of the currently selected target, if any.
 * @param {(t: string) => void} setPendingTarget - Setter invoked to mark a target as pending selection.
 * @param {(t: string) => void} [setSelectedTarget] - Optional setter invoked to confirm the selected target.
 * @param {Set<string>} [remoteTargets] - Remote ingestion folder names used to compute highlighted IDs.
 * @param {boolean} [filterProcessedOnly] - When true, restricts the list to targets with a processed/stacked image.
 * @param {boolean} [includeStars] - When true, adds a "Stars" filter option and includes the star list.
 * @param {boolean} [disableAutoSelect] - When true, suppresses auto-selecting the first filtered item.
 * @returns {object} List items, raw targets/stars, filter state and setters, highlighted IDs, and isLocalTarget.
 */
export const useTargetListLogic = (
    reloadKey: number | undefined,
    pendingTarget: string | undefined,
    selectedTarget: string | undefined,
    setPendingTarget: (t: string) => void,
    setSelectedTarget?: (t: string) => void,
    remoteTargets: Set<string> = new Set(),
    filterProcessedOnly: boolean = false,
    includeStars: boolean = false,
    disableAutoSelect: boolean = false
) => {
    const [dropdown, setDropdown] = useState<string>('Target Objects');
    const [filterText, setFilterText] = useState<string>('');
    const toast = useToast();

    // Shared queries: multiple views consume the same cached target/star
    // lists instead of each independently fetching them on mount.
    const targetListQuery = useTargetListQuery();
    const astronomyListQuery = useAstronomyListQuery();
    const targets = useMemo(() => (targetListQuery.data as Target[]) ?? [], [targetListQuery.data]);
    const stars = useMemo(() => astronomyListQuery.data ?? [], [astronomyListQuery.data]);

    // Callers change reloadKey (e.g. after a new ingestion) to force a
    // refresh. Force a refetch on every change except the initial mount,
    // since the queries already fetch automatically on first use.
    const isFirstReloadKeyRender = useRef(true);
    useEffect(() => {
        if (isFirstReloadKeyRender.current) {
            isFirstReloadKeyRender.current = false;
            return;
        }
        targetListQuery.refetch();
        astronomyListQuery.refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reloadKey]);

    useEffect(() => {
        if (!targetListQuery.error) return;
        console.error(targetListQuery.error);
        try {
            toast.show(
                targetListQuery.error instanceof Error
                    ? targetListQuery.error.message
                    : String(targetListQuery.error),
                'error'
            );
        } catch {
            // Ignore toast errors
        }
    }, [targetListQuery.error, toast]);

    useEffect(() => {
        if (astronomyListQuery.error) {
            console.error(astronomyListQuery.error);
        }
    }, [astronomyListQuery.error]);

    // Computed highlighted IDs based on fuzzy matching between targets and remote folders
    const highlightedIds = useMemo(() => {
        const highlights = new Set<string>();
        if (remoteTargets.size === 0 || targets.length === 0) return highlights;

        const normalize = (s: string) => s.replace(/[_\s]/g, '').toLowerCase();
        const remoteNorm = new Set(Array.from(remoteTargets).map(normalize));

        targets.forEach((t) => {
            const val = typeof t === 'string' ? t : String(t.id || t.name || '');
            if (remoteNorm.has(normalize(val))) {
                highlights.add(val);
            }
        });
        return highlights;
    }, [targets, remoteTargets]);

    // Filtering Logic
    const applyDropdownFilter = useCallback(
        (t: Target) => {
            const processedPath = typeof t === 'string'
                ? ''
                : (t.processed_image || t.processedImage || '');
            const isProcessed = typeof processedPath === 'string' && processedPath.trim() !== '';

            if (dropdown === 'No Image') {
                return !isProcessed;
            }

            // By default, hide targets without a processed image in standard categories
            if (!isProcessed) {
                return false;
            }

            const nameRaw = typeof t === 'string' ? t : String(t.name ?? t.id ?? '');
            const name = nameRaw.replace(/\u00A0/g, ' ').replace(/_/g, ' ').trim();
            if (!dropdown || dropdown === 'Target Objects') return true;
            if (dropdown === 'Messier Objects') {
                return /^M(?=[\s\d]|$)/i.test(name);
            }
            if (dropdown === 'NGC IC Objects') {
                return /^NGC(?=[\s\d]|$)/i.test(name) || /^IC(?=[\s\d]|$)/i.test(name);
            }
            return true;
        },
        [dropdown]
    );

    const applyTextFilter = useCallback(
        (t: Target) => {
            if (!filterText || filterText.trim() === '') return true;
            const needle = filterText.trim().toLowerCase();
            const nameRaw = typeof t === 'string' ? t : String(t.name ?? t.id ?? '');
            const name = nameRaw.replace(/\u00A0/g, ' ').replace(/_/g, ' ').trim().toLowerCase();
            return name.includes(needle);
        },
        [filterText]
    );

    const applyProcessedFilter = useCallback(
        (t: Target) => {
            if (!filterProcessedOnly) return true;
            const processedPath = typeof t === 'string'
                ? ''
                : (t.processed_image || t.processedImage || '');
            return typeof processedPath === 'string' && processedPath.trim() !== '';
        },
        [filterProcessedOnly]
    );

    // Memoized Filtered Targets
    const filteredTargets = useMemo(() => {
        if (dropdown === 'Stars') {
            if (!filterText || filterText.trim() === '') return stars;
            const needle = filterText.trim().toLowerCase();
            return stars.filter((star: any) => {
                const nameRaw = String(star.name ?? star.id ?? '');
                const name = nameRaw.replace(/\u00A0/g, ' ').replace(/_/g, ' ').trim().toLowerCase();
                return name.includes(needle);
            });
        }
        return targets.filter((t) => applyDropdownFilter(t) && applyTextFilter(t) && applyProcessedFilter(t));
    }, [targets, stars, dropdown, applyDropdownFilter, applyTextFilter, applyProcessedFilter, filterText]);

    // Auto-selection Logic
    useEffect(() => {
        if (disableAutoSelect) return;
        if (filteredTargets.length > 0 && !pendingTarget && !selectedTarget) {
            const first = filteredTargets[0];
            const firstId = typeof first === 'string'
                ? first
                : (first.id || first.name || 'unknown');
            const newId = String(firstId);
            setPendingTarget(newId);
            if (setSelectedTarget) {
                setSelectedTarget(newId);
            }
        }
    }, [filteredTargets, selectedTarget, pendingTarget, setPendingTarget, setSelectedTarget, disableAutoSelect]);

    // Output formatting - Memoized
    const filteredItems: SelectableItem[] = useMemo(() => {
        return filteredTargets.map((target: any) => {
            let value: string;
            let label: string;

            if (typeof target === 'string') {
                value = target;
                label = target.replace(/_/g, ' ');
            } else {
                value = String(target.id || target.name || '');
                label = String(target.name || target.id || '').replace(/_/g, ' ');

                // Final fallback if object is empty or missing expected fields
                if (!value) value = 'unknown';
                if (!label) label = 'Unknown Object';
            }

            return {
                id: value,
                value: value,
                label: label,
                isProcessed: dropdown === 'Stars'
                    ? undefined
                    : (typeof target === 'string'
                        ? false
                        : typeof (target.processed_image || target.processedImage) === 'string' &&
                          (target.processed_image || target.processedImage).trim() !== '')
            };
        });
    }, [filteredTargets, dropdown]);

    const filterOptions = useMemo(() => {
        const options = [...BASE_FILTER_OPTIONS];
        if (includeStars) {
            options.push('Stars');
        }
        return options;
    }, [includeStars]);

    return {
        items: filteredItems,
        targets,
        stars,
        filterOptions,
        selectedFilterOption: dropdown,
        setFilterOption: setDropdown,
        filterText,
        setFilterText,
        highlightedIds: highlightedIds,
        isLocalTarget: !!selectedTarget && (dropdown === 'Stars' ? stars : targets).some(t => {
            const val = typeof t === 'string' ? t : String(t.id || t.name || '');
            const normalize = (s: string) => s.replace(/[_\s]/g, '').toLowerCase();
            return normalize(val) === normalize(selectedTarget);
        })
    };
};
