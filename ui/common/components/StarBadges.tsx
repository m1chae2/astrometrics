/**
 * @fileoverview StarBadges: the chips that say what type of star it is (catalog type, type matched from the
 * star's own spectrum) and which repeating brightness pattern was found. The Astronomy Manager's star card and
 * the FITS viewer's star inspector both show them, so the two screens read the same.
 */

import React from 'react';
import { Badge } from './Badge';
import type { BadgeTone } from './Badge';
import { describePatternBadges, describeStarTypeBadges } from '../utils/starDisplayFormat';
import type { StarBadge } from '../utils/starDisplayFormat';
import type { Spectrum } from '../types/backendTypes';

/** The badge color for each kind of star chip. */
const TONE_FOR_KIND: Record<StarBadge['tone'], BadgeTone> = {
    catalog: 'info',
    measured: 'ok',
    caution: 'warning',
    neutral: 'neutral',
    pattern: 'warning',
};

export interface StarBadgesProps {
    /** The star's record from `astronomy:get`, or null when it is not known. */
    star: Pick<Spectrum, 'spectralType' | 'spectroscopy' | 'photometry'> | null | undefined;
}

/**
 * Shows the type chips and the pattern chips of one star.
 *
 * @param props See {@link StarBadgesProps}.
 * @return The chips, or nothing when no star is given.
 */
export const StarBadges: React.FC<StarBadgesProps> = ({ star }) => {
    if (!star) return null;
    const chips = [
        ...describeStarTypeBadges(star.spectralType, star.spectroscopy),
        ...describePatternBadges(star.photometry),
    ];
    return (
        <>
            {chips.map((chip) => (
                <Badge key={chip.text} tone={TONE_FOR_KIND[chip.tone]} title={chip.title}>{chip.text}</Badge>
            ))}
        </>
    );
};
