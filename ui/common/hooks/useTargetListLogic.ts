import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTargetListQuery } from '../queries/useTargetListQuery';
import { useAstronomyListQuery } from '../queries/useAstronomyListQuery';
import { reportError } from '../utils/reportError';
import { useTargetCameraIndexQuery } from '../queries/useTargetCameraIndexQuery';
import { SelectableItem } from '../components/SelectableList';
import { updateTargetListFilter, useTargetListFilterState } from '../state/targetListFilterStore';
import {
    CAMERA_ALL,
    CATALOG_ALL,
    CATALOG_MESSIER,
    CATALOG_NGC_IC,
    CATALOG_NO_IMAGE,
    CATALOG_STARS,
    SORT_NEWEST,
    TargetListEntry,
    cleanTargetName,
    formatLastImaged,
    hasProcessedImage,
    lastImagedTime,
    matchesCamera,
    matchesCatalog,
    sortTargets,
    targetListId,
} from './targetListFiltering';
import type { TargetListFilterPanelProps } from '../radioList/TargetListFilterPanel';

interface Target extends TargetListEntry {
    [key: string]: unknown;
}

/**
 * Fetches, filters, sorts, and manages selection state for the target/star radio list
 * shown in the left-hand panel of the Image Processing, Image Viewer, Observatory
 * and Observation displays.
 *
 * Fetches the target list (and, lazily, the star list) from the backend and applies
 * the shared catalog, camera, sort and text filters. The catalog, camera and sort
 * choices are shared by every screen and saved between sessions. Also computes
 * fuzzy-matched highlighted IDs against remote ingestion folders, and auto-selects
 * the first filtered item when nothing is already selected or pending. A selected
 * target stays selected even when the filters hide it.
 *
 * @param {number | undefined} reloadKey - Changing this value re-triggers the target/star fetch.
 * @param {string | undefined} pendingTarget - ID of a target awaiting confirmation of selection.
 * @param {string | undefined} selectedTarget - ID of the currently selected target, if any.
 * @param {(t: string) => void} setPendingTarget - Setter invoked to mark a target as pending selection.
 * @param {(t: string) => void} [setSelectedTarget] - Optional setter invoked to confirm the selected target.
 * @param {Set<string>} [remoteTargets] - Remote ingestion folder names used to compute highlighted IDs.
 * @param {boolean} [filterProcessedOnly] - When true, restricts the list to targets with a processed/stacked image.
 * @param {boolean} [includeStars] - When true, adds a "Stars" catalog choice and includes the star list.
 * @param {boolean} [disableAutoSelect] - When true, suppresses auto-selecting the first filtered item.
 * @returns {object} List items, raw targets/stars, filter panel props, text filter state, highlighted IDs, and isLocalTarget.
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
    const { catalog: savedCatalog, camera: savedCamera, sort } = useTargetListFilterState();
    const [filterText, setFilterText] = useState<string>('');

    // Shared queries: multiple views consume the same cached target/star
    // lists instead of each independently fetching them on mount.
    const targetListQuery = useTargetListQuery();
    const astronomyListQuery = useAstronomyListQuery();
    const cameraIndexQuery = useTargetCameraIndexQuery();
    const targets = useMemo(() => (targetListQuery.data as Target[]) ?? [], [targetListQuery.data]);
    const stars = useMemo(() => astronomyListQuery.data ?? [], [astronomyListQuery.data]);
    const cameraIndex = cameraIndexQuery.data;

    const catalogOptions = useMemo(() => {
        const options = [CATALOG_ALL, CATALOG_MESSIER, CATALOG_NGC_IC, CATALOG_NO_IMAGE];
        if (includeStars) options.push(CATALOG_STARS);
        return options;
    }, [includeStars]);

    // A saved choice can name something this screen (or the current
    // equipment config) no longer offers; fall back to "no filter" then.
    const catalog = catalogOptions.includes(savedCatalog) ? savedCatalog : CATALOG_ALL;
    const configuredCameraNames = useMemo(
        () => (cameraIndex?.cameras ?? []).map((cameraSummary) => cameraSummary.name),
        [cameraIndex]
    );
    const camera = configuredCameraNames.includes(savedCamera) ? savedCamera : CAMERA_ALL;

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
        cameraIndexQuery.refetch();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reloadKey]);

    useEffect(() => {
        if (!targetListQuery.error) return;
        reportError(targetListQuery.error, 'target-list');
    }, [targetListQuery.error]);

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
            const val = typeof t === 'string' ? t : targetListId(t);
            if (remoteNorm.has(normalize(val))) {
                highlights.add(val);
            }
        });
        return highlights;
    }, [targets, remoteTargets]);

    const applyTextFilter = useCallback(
        (t: Target) => {
            if (!filterText || filterText.trim() === '') return true;
            const needle = filterText.trim().toLowerCase();
            const nameRaw = typeof t === 'string' ? t : String(t.name ?? t.id ?? '');
            return cleanTargetName(nameRaw).toLowerCase().includes(needle);
        },
        [filterText]
    );

    // Memoized Filtered Targets
    const filteredTargets = useMemo(() => {
        if (catalog === CATALOG_STARS) {
            if (!filterText || filterText.trim() === '') return stars;
            const needle = filterText.trim().toLowerCase();
            return stars.filter((star: any) => {
                const nameRaw = String(star.name ?? star.id ?? '');
                return cleanTargetName(nameRaw).toLowerCase().includes(needle);
            });
        }
        const matching = targets.filter(
            (t) =>
                matchesCatalog(t, catalog) &&
                matchesCamera(t, camera, cameraIndex) &&
                applyTextFilter(t) &&
                (!filterProcessedOnly || hasProcessedImage(t))
        );
        return sortTargets(matching, sort, camera, cameraIndex);
    }, [targets, stars, catalog, camera, cameraIndex, sort, applyTextFilter, filterText, filterProcessedOnly]);

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

            const isStarRow = catalog === CATALOG_STARS;
            return {
                id: value,
                value: value,
                label: label,
                isProcessed: isStarRow
                    ? undefined
                    : (typeof target === 'string' ? false : hasProcessedImage(target)),
                // Under "Newest" the date explains the order.
                subtitle: !isStarRow && sort === SORT_NEWEST && typeof target !== 'string'
                    ? formatLastImaged(lastImagedTime(target, camera, cameraIndex)) ?? 'No frames'
                    : undefined,
            };
        });
    }, [filteredTargets, catalog, camera, cameraIndex, sort]);

    const filterPanel: TargetListFilterPanelProps = useMemo(
        () => ({
            catalogOptions,
            selectedCatalog: catalog,
            onCatalogChange: (nextCatalog: string) => updateTargetListFilter({ catalog: nextCatalog }),
            cameras: cameraIndex?.cameras ?? [],
            selectedCamera: camera,
            onCameraChange: (nextCamera: string) => updateTargetListFilter({ camera: nextCamera }),
            sort,
            onSortChange: (nextSort) => updateTargetListFilter({ sort: nextSort }),
        }),
        [catalogOptions, catalog, cameraIndex, camera, sort]
    );

    return {
        items: filteredItems,
        targets,
        stars,
        isLoading: catalog === CATALOG_STARS ? astronomyListQuery.isLoading : targetListQuery.isLoading,
        filterPanel,
        filterText,
        setFilterText,
        highlightedIds: highlightedIds,
        isLocalTarget: !!selectedTarget && (catalog === CATALOG_STARS ? stars : targets).some(t => {
            const val = typeof t === 'string' ? t : targetListId(t);
            const normalize = (s: string) => s.replace(/[_\s]/g, '').toLowerCase();
            return normalize(val) === normalize(selectedTarget);
        })
    };
};
