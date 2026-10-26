/**
 * @fileoverview The text chip that states how well a result was measured.
 */

import React from 'react';
import { Confidence } from '../../types/backendTypes';
import { confidenceWord } from './statementLabels';

export interface ConfidenceChipProps {
    /** The confidence the library gave. */
    confidence: Confidence;
}

/**
 * Shows "Measured", "Partly measured" or "Tentative" as a text chip.
 * @param props The confidence.
 * @return The chip.
 */
export const ConfidenceChip: React.FC<ConfidenceChipProps> = ({ confidence }) => (
    <span className={`confidence-chip confidence-chip--${confidence}`}>{confidenceWord(confidence)}</span>
);
