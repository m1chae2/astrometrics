/**
 * @fileoverview The text chip that states how well a result was measured.
 */

import React from 'react';
import { Confidence } from '../../types/backendTypes';
import { confidenceWord } from './statementLabels';

export interface ConfidenceChipProps {
    /** The confidence the library gave. */
    confidence: Confidence;
    /** Use the long form "Tentative (design estimate)". */
    expanded?: boolean;
}

/**
 * Shows "Measured", "Partly measured" or "Tentative" as a text chip.
 * @param props The confidence and the form to use.
 * @return The chip.
 */
export const ConfidenceChip: React.FC<ConfidenceChipProps> = ({ confidence, expanded = false }) => (
    <span className={`confidence-chip confidence-chip--${confidence}`}>{confidenceWord(confidence, expanded)}</span>
);
