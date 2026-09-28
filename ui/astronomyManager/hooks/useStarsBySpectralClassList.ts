import { useMemo, useState } from 'react';
import { useStarsBySpectralClassQuery } from '../../common/queries/useStarsBySpectralClassQuery';
import { SelectableItem } from '../../common/components/SelectableList';
import { formatSpectralMatchQuality, formatStarListLabel } from '../utils/starDisplayFormat';

/**
 * Fetches the stars in one catalog spectral class, best self-determined
 * match first (already sorted by the backend), with a local text filter.
 *
 * @param spectralClass The spectral class letter, or undefined when no class is selected.
 * @returns The class's star list items and a local name filter with its setter.
 */
export const useStarsBySpectralClassList = (spectralClass: string | undefined) => {
    const [filterText, setFilterText] = useState<string>('');

    const starsQuery = useStarsBySpectralClassQuery(spectralClass);
    const stars = useMemo(() => starsQuery.data ?? [], [starsQuery.data]);

    const items: SelectableItem[] = useMemo(() => {
        const needle = filterText.trim().toLowerCase();
        const matching = needle
            ? stars.filter((star) => `${star.name} ${star.id}`.toLowerCase().includes(needle))
            : stars;

        return matching.map((star) => {
            const fullName = star.name || star.id;
            const tooltip = fullName === star.id ? fullName : `${fullName} (${star.id})`;
            const subtitleParts = [
                star.spectralType,
                formatSpectralMatchQuality(star.selfDeterminedSpectralTypeRms),
            ].filter((part) => part !== '');

            return {
                id: star.id,
                value: star.id,
                label: formatStarListLabel(fullName),
                subtitle: subtitleParts.join(' · '),
                tooltip,
                hasSpectra: star.hasSpectra,
                hasPhotometry: star.hasPhotometry,
                ra: star.ra ?? undefined,
                dec: star.dec ?? undefined,
            };
        });
    }, [stars, filterText]);

    return { items, filterText, setFilterText, isLoading: starsQuery.isLoading };
};
