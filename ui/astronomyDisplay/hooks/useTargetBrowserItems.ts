import { useMemo, useState } from 'react';
import { useTargetListQuery } from '../../common/queries/useTargetListQuery';
import { useTargetDataAvailabilityQuery } from '../../common/queries/useTargetDataAvailabilityQuery';
import { SelectableItem } from '../../common/components/SelectableList';

/**
 * Builds the "browse by target" primary list: every target, each showing
 * its star count and whether it has spectra/photometry. There is no
 * unscoped "All" entry -- this mode is for drilling into one target's
 * stars, not browsing the whole catalog (use the "All" spectral class for
 * that instead).
 *
 * @returns The target list items, a local name filter, and its setter.
 */
export const useTargetBrowserItems = () => {
    const [filterText, setFilterText] = useState<string>('');

    const targetListQuery = useTargetListQuery();
    const targets = useMemo(() => (targetListQuery.data as any[]) ?? [], [targetListQuery.data]);

    const targetDataAvailabilityQuery = useTargetDataAvailabilityQuery();
    const targetDataAvailability = useMemo(
        () => targetDataAvailabilityQuery.data ?? {},
        [targetDataAvailabilityQuery.data]
    );
    // A target with no entry in the availability map is ambiguous while the
    // map is still loading: it might genuinely have zero stars, or the
    // full-catalog scan behind it (a couple of seconds on a large library)
    // just hasn't resolved yet. Without this, every target showed "0
    // stars" for that entire window, which reads as broken rather than
    // loading.
    const isAvailabilityLoading = targetDataAvailabilityQuery.isLoading;

    const items: SelectableItem[] = useMemo(() => {
        const targetNames = targets
            .map((t: any) => (typeof t === 'string' ? t : String(t.name || t.id || '')))
            .filter((n: string) => n.trim() !== '');
        const uniqueTargetNames = Array.from(new Set(targetNames)).sort();

        const needle = filterText.trim().toLowerCase();
        const matching = needle
            ? uniqueTargetNames.filter((name) => name.toLowerCase().includes(needle))
            : uniqueTargetNames;

        return matching.map((name) => {
            const availability = targetDataAvailability[name];
            const subtitle = isAvailabilityLoading
                ? 'Loading…'
                : `${availability?.starCount ?? 0} star${(availability?.starCount ?? 0) === 1 ? '' : 's'}`;
            return {
                id: name,
                value: name,
                label: name,
                subtitle,
                hasSpectra: !!availability?.hasSpectra,
                hasPhotometry: !!availability?.hasPhotometry,
            };
        });
    }, [targets, targetDataAvailability, isAvailabilityLoading, filterText]);

    return { items, filterText, setFilterText };
};
