/**
 * @file test_StarSummaryCardAliases.test.tsx
 * @description Tests that the star card lists the star's other names: a few shown, the rest behind "+N more".
 */
import React from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { StarSummaryCard } from '../components/StarSummaryCard';
import { clearImageView, saveImageView } from '../../common/utils/imageViewStore';
import { peekHandoffs } from '../../common/utils/displayHandoffStore';
import { describeAliases } from '../../common/utils/starDisplayFormat';

describe('describeAliases', () => {
  it('drops the star\'s own name, repeats and the NAME prefix, and keeps the given order', () => {
    const result = describeAliases(['NAME Vega', 'vega', '* alf Lyr', 'HD 172167', 'HIP 91262', 'Gaia DR3 2'], 'Vega');
    expect(result.shown).toEqual(['* alf Lyr', 'HD 172167', 'HIP 91262']);
    expect(result.hidden).toEqual(['Gaia DR3 2']);
  });

  it('gives nothing when there are no aliases', () => {
    expect(describeAliases(undefined, 'Vega')).toEqual({ shown: [], hidden: [] });
  });
});

describe('StarSummaryCard aliases', () => {
  it('shows three names and "+N more" with the rest in the tooltip', () => {
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega', aliases: ['alf Lyr', 'HD 172167', 'HIP 91262', 'HR 7001', 'Gaia DR3 1'] }} />);
    expect(screen.getByTestId('star-aliases').textContent).toContain('alf Lyr, HD 172167, HIP 91262');
    const more = screen.getByTestId('star-aliases-more');
    expect(more.textContent).toBe('+2 more');
    expect(more.getAttribute('title')).toBe('HR 7001, Gaia DR3 1');
  });

  it('shows no line when the star has no other names', () => {
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega' }} />);
    expect(screen.queryByTestId('star-aliases')).toBeNull();
  });
});

describe('StarSummaryCard Back to image', () => {
  beforeEach(() => {
    clearImageView();
    window.localStorage.clear();
  });

  it('shows no button when the person did not come from the Image Processing picture', () => {
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega' }} />);
    expect(screen.queryByRole('button', { name: 'Back to image' })).toBeNull();
  });

  it('shows the button after a star was opened from the picture, and asks Image Processing to put the view back', () => {
    saveImageView({
      target: 'M 81', file: '/stack/M81.fits', displayMode: 'linear', astrometry: true, detections: false,
      view: { x: 0.4, y: 0.5, zoomOverFit: 3, turn: 0 }, returnPending: true, arrivedFromStar: 'Vega',
    });
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Back to image' }));
    expect(window.localStorage.getItem('appMode')).toBe('Image Processing');
    expect(peekHandoffs('Image Processing', 'returnToImage')).toHaveLength(1);
  });
});
