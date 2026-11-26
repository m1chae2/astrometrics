/**
 * @module StarColorLegend
 * @fileoverview The small color key shown on the sky map while the
 * Planetarium browses by spectral class.
 */

import React from 'react';
import { Button } from '../../common/components/Button';
import { STAR_COLOR_LEGEND } from '../utils/starColor';
import '../styles/starColorLegend.css';

/** Props for StarColorLegend. */
export interface StarColorLegendProps {
  /** How many colored stars are drawn. */
  shownCount: number;
  /** The chosen class letter, or undefined for all classes. */
  spectralClass?: string;
  /** Whether the stars are still loading. */
  isLoading: boolean;
  /** Whether the stars could not be loaded. */
  isError: boolean;
  /** Asks for the stars again. */
  onRetry: () => void;
}

/**
 * Shows one colored dot per spectral class, hottest first, and how many
 * colored stars are drawn.
 *
 * @func StarColorLegend
 * @param {StarColorLegendProps} props The count and loading state.
 * @returns {React.ReactElement} The legend.
 */
export const StarColorLegend: React.FC<StarColorLegendProps> = ({ shownCount, spectralClass, isLoading, isError, onRetry }) => {
  let note: React.ReactNode;
  if (isError) {
    note = (
      <>
        Could not load the stars.{' '}
        <Button size="small" onClick={onRetry}>Try again</Button>
      </>
    );
  } else if (isLoading) {
    note = 'Loading the stars…';
  } else {
    const count = `${shownCount.toLocaleString('en-US')} star${shownCount === 1 ? '' : 's'}`;
    note = spectralClass ? `${count} in class ${spectralClass}` : `${count} by class`;
  }

  return (
    <div className="star-color-legend" role="group" aria-label="Star colors by spectral class">
      <div className="star-color-legend__title">Star color by class</div>
      <ul className="star-color-legend__list">
        {STAR_COLOR_LEGEND.map((entry) => (
          <li key={entry.colorClass} className="star-color-legend__entry" data-color-class={entry.colorClass}>
            <span
              className={`star-color-legend__swatch star-color-legend__swatch--${entry.colorClass.toLowerCase()}`}
              aria-hidden="true"
            />
            {entry.label}
          </li>
        ))}
      </ul>
      <div className="star-color-legend__note">{note}</div>
    </div>
  );
};
