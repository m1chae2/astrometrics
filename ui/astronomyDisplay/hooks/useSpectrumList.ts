import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAstronomyListQuery } from '../../common/queries/useAstronomyListQuery';
import { useTargetListQuery } from '../../common/queries/useTargetListQuery';
import { useToast } from '../../common/hooks/useToast';
import { SelectableItem } from '../../common/components/SelectableList';
import { Spectrum } from '../../common/types/backendTypes';

const BASE_FILTER_OPTIONS = [
    'All',
    'With Spectra',
    'With Photometry'
];

export const useSpectrumList = (
    reloadKey?: number,
    pendingId?: string,
    selectedId?: string,
    setPendingId?: (t: string) => void
) => {
    const [dropdown, setDropdown] = useState<string>('All');
    const [filterText, setFilterText] = useState<string>('');
    const [debouncedFilterText, setDebouncedFilterText] = useState<string>('');
    const toast = useToast();

    // Debounce search text input by 250ms to prevent excessive backend queries
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedFilterText(filterText);
        }, 250);
        return () => clearTimeout(timer);
    }, [filterText]);

    // Fetch target list to dynamically populate target filter options
    const targetListQuery = useTargetListQuery();
    const targets = useMemo(() => (targetListQuery.data as any[]) ?? [], [targetListQuery.data]);

    // Compute dynamic filter options with Target: <name> items
    const filterOptions = useMemo(() => {
        const targetNames = targets
            .map((t: any) => (typeof t === 'string' ? t : String(t.name || t.id || '')))
            .filter((n: string) => n.trim() !== '');

        const uniqueTargetNames = Array.from(new Set(targetNames)).sort();
        const targetOpts = uniqueTargetNames.map((name) => `Target: ${name}`);
        return [...BASE_FILTER_OPTIONS, ...targetOpts];
    }, [targets]);

    // Extract active target ID if a target option is selected in the dropdown
    const activeTargetId = useMemo(() => {
        if (dropdown.startsWith('Target: ')) {
            return dropdown.replace('Target: ', '').trim();
        }
        return undefined;
    }, [dropdown]);

    // Extract active capability category filter
    const activeFilterType = useMemo(() => {
        if (dropdown === 'With Spectra') return 'With Spectra';
        if (dropdown === 'With Photometry') return 'With Photometry';
        return undefined;
    }, [dropdown]);

    // Fetch astronomy list capped at 100 stars with full database search
    const queryOptions = useMemo(() => ({
        targetId: activeTargetId,
        search: debouncedFilterText,
        filterType: activeFilterType,
        limit: 100,
    }), [activeTargetId, debouncedFilterText, activeFilterType]);

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
        targetListQuery.refetch();
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

    // Filtering Logic
    const applyTextFilter = useCallback(
        (s: Spectrum) => {
            if (!filterText || filterText.trim() === '') return true;
            const needle = filterText.trim().toLowerCase();
            const val = typeof s === 'string' ? s : `${s.name || ''} ${s.label || ''} ${s.id || ''}`;
            return val.toLowerCase().includes(needle);
        },
        [filterText]
    );

    // Category filtering
    const checkHasSpectra = useCallback((s: Spectrum): boolean => {
        if (typeof s === 'string') return false;
        return (
            !!s.hasSpectra ||
            !!s.has_spectra ||
            (Array.isArray(s.spectraHistory) && s.spectraHistory.length > 0) ||
            (Array.isArray(s.spectrumData) && s.spectrumData.length > 0) ||
            (Array.isArray(s.data) && s.data.length > 0) ||
            !!s.spectrumDataProcessed
        );
    }, []);

    const checkHasPhotometry = useCallback((s: Spectrum): boolean => {
        if (typeof s === 'string') return false;
        return (
            !!s.hasPhotometry ||
            !!s.has_photometry ||
            (!!s.lightCurve &&
                ((Array.isArray(s.lightCurve.timestamps) && s.lightCurve.timestamps.length > 0) ||
                 (Array.isArray(s.lightCurve.magnitudes) && s.lightCurve.magnitudes.length > 0) ||
                 (Array.isArray(s.lightCurve.fluxes) && s.lightCurve.fluxes.length > 0)))
        );
    }, []);

    const applyCategoryFilter = useCallback(
        (s: Spectrum) => {
            if (dropdown.startsWith('Target: ')) {
                const targetName = dropdown.replace('Target: ', '').trim().toLowerCase();
                const starTargetIds = typeof s === 'object' && s !== null
                    ? (s.targetIds || s.target_ids || [])
                    : [];
                if (Array.isArray(starTargetIds) && starTargetIds.some((t: string) => String(t).toLowerCase() === targetName)) {
                    return true;
                }

                const starTargetId = typeof s === 'object' && s !== null
                    ? String(s.targetId || s.target_id || s.target || '').toLowerCase()
                    : '';
                if (starTargetId) {
                    return starTargetId === targetName;
                }

                const starId = typeof s === 'string' ? (s as string).toLowerCase() : String(s.id || s.label || '').toLowerCase();
                const normalizedTarget = targetName.replace(/[\s_]/g, '');
                const normalizedStar = starId.replace(/[\s_]/g, '');
                return normalizedStar.startsWith(normalizedTarget);
            }

            if (dropdown === 'Latest Analysis') {
                const latestId = localStorage.getItem('latestAnalysisTargetId');
                if (!latestId) return false;
                return s.targetIds?.includes(latestId) ?? false;
            }
            if (dropdown === 'With Spectra') {
                return checkHasSpectra(s);
            }
            if (dropdown === 'With Photometry') {
                return checkHasPhotometry(s);
            }
            return true;
        },
        [dropdown, checkHasSpectra, checkHasPhotometry]
    );

    const latestTargetId = localStorage.getItem('latestAnalysisTargetId');

    const filteredItems: SelectableItem[] = spectra
        .filter((s) => {
            const name = typeof s === 'string' ? s : (s.name || s.id || s.label || '');
            const idStr = typeof s === 'string' ? s : (s.id || s.name || s.label || '');
            if (name.startsWith('Star_') || name.startsWith('Star ') || idStr.startsWith('Star_') || idStr.startsWith('Star ')) {
                return false;
            }
            return true;
        })
        .filter((s) => applyCategoryFilter(s))
        .filter((s) => applyTextFilter(s))
        .slice(0, 100)
        .map((s) => {
            const objectId = typeof s === 'string' ? s : (s.id || s.name || s.label || '');
            const value = objectId;
            const label = typeof s === 'string' ? s : (s.name || s.label || s.id || '');




            const hasSpectra = checkHasSpectra(s);
            const hasPhotometry = checkHasPhotometry(s);

            return {
                id: value,
                value: value,
                label: label,
                hasSpectra,
                hasPhotometry
            };
        });

    const highlightedIds = new Set<string>(
        spectra
            .filter(s => latestTargetId && s.targetIds?.includes(latestTargetId))
            .map(s => String(s.id || s.label || s))
    );

    return {
        items: filteredItems,
        filterOptions,
        selectedFilterOption: dropdown,
        setFilterOption: setDropdown,
        filterText,
        setFilterText,
        highlightedIds,
    };
};
