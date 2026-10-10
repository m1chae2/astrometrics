import { useMemo, useState } from 'react';
import { useSpectralClassSummaryQuery } from '../../common/queries/useSpectralClassSummaryQuery';
import { SelectableItem } from '../../common/components/SelectableList';

/** Pseudo-class selection meaning "the whole catalog, unscoped". */
export const ALL_SPECTRAL_CLASSES_VALUE = 'All';

/**
 * Builds the "browse by spectral class" primary list: every catalog
 * spectral class present in the library, each showing its star count,
 * plus a leading "All" entry for the unscoped catalog.
 *
 * The class summary is a full catalog scan (a couple of seconds on a
 * large library), so `isLoading` is surfaced alongside the items: without
 * it, the list under "All" looks simply empty rather than still loading.
 *
 * @returns The spectral class list items, a local name filter and its setter, and a loading flag.
 */
export const useSpectralClassBrowserItems = () => {
    const [filterText, setFilterText] = useState<string>('');

    const spectralClassSummaryQuery = useSpectralClassSummaryQuery();
    const classes = useMemo(() => spectralClassSummaryQuery.data ?? [], [spectralClassSummaryQuery.data]);

    const items: SelectableItem[] = useMemo(() => {
        const needle = filterText.trim().toLowerCase();
        const matching = needle
            ? classes.filter(
                  (c) => c.spectralClass.toLowerCase().includes(needle) || c.label.toLowerCase().includes(needle)
              )
            : classes;

        const classItems = matching.map((c) => ({
            id: c.spectralClass,
            value: c.spectralClass,
            label: `${c.spectralClass} — ${c.label}`,
            subtitle: `${c.count} star${c.count === 1 ? '' : 's'}`,
        }));

        return [
            { id: ALL_SPECTRAL_CLASSES_VALUE, value: ALL_SPECTRAL_CLASSES_VALUE, label: 'All' },
            ...classItems,
        ];
    }, [classes, filterText]);

    return { items, filterText, setFilterText, isLoading: spectralClassSummaryQuery.isLoading };
};
