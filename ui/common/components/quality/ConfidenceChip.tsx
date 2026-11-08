/**
 * @fileoverview The text chip that states how well a result was measured.
 */

import React from 'react';
import { Confidence } from '../../types/backendTypes';
import { confidenceWord } from './statementLabels';
import { Badge, BadgeTone } from '../Badge';

export interface ConfidenceChipProps {
    /** The confidence the library gave. */
    confidence: Confidence;
}

/** The tone and edge of each confidence. The edge repeats what the word says. */
const CONFIDENCE_STYLE: Record<Confidence, { tone: BadgeTone; edge: 'solid' | 'dashed' | 'dotted' }> = {
    measured: { tone: 'ok', edge: 'solid' },
    partly_measured: { tone: 'warning', edge: 'dashed' },
    design_estimate: { tone: 'info', edge: 'dotted' },
};

/**
 * Shows "Measured", "Partly measured" or "Tentative" as a text chip.
 * @param props The confidence.
 * @return The chip.
 */
export const ConfidenceChip: React.FC<ConfidenceChipProps> = ({ confidence }) => (
    <Badge
        tone={CONFIDENCE_STYLE[confidence].tone}
        edge={CONFIDENCE_STYLE[confidence].edge}
        className={`confidence-chip confidence-chip--${confidence}`}
    >
        {confidenceWord(confidence)}
    </Badge>
);
