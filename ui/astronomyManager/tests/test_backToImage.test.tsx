/**
 * @file test_backToImage.test.tsx
 * @description Checks the Astronomy Manager's "Back to image" button: the same star the person came from puts the
 * saved Image Processing view back, and another star (or a star of another target) is handed to Image Processing
 * with the target it belongs to now, so it opens there on the main stack.
 */
import React from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { StarSummaryCard } from '../components/StarSummaryCard';
import { clearImageView, saveImageView, type SavedImageView } from '../../common/utils/imageViewStore';
import { peekHandoffs } from '../../common/utils/displayHandoffStore';
import { planBackToImage, targetForStar } from '../utils/backToImage';

/** A view saved after the person opened Vega from the M 31 stack. */
const savedFromVega: SavedImageView = {
  target: 'M 31', file: '/stacks/M31.fits', displayMode: 'linear', astrometry: true, detections: false,
  view: { x: 0.4, y: 0.5, zoomOverFit: 3, turn: 0 }, returnPending: true, arrivedFromStar: 'Gaia 1', arrivedFromRecord: 'vega-id',
};

describe('planBackToImage', () => {
  it('puts the saved view back for the same star under the same target', () => {
    expect(planBackToImage(savedFromVega, 'vega-id', 'Vega', ['M 31'], 'M 31')).toEqual({ kind: 'restore' });
  });

  it('opens another star on its target', () => {
    expect(planBackToImage(savedFromVega, 'deneb-id', 'Deneb', ['M 31'], 'M 31')).toEqual({
      kind: 'focus',
      handoff: { targetId: 'M 31', enableAstrometry: true, focusLibraryStarId: 'deneb-id', focusStarName: 'Deneb' },
    });
  });

  it('follows a switch of target in the Astronomy Manager', () => {
    const plan = planBackToImage(savedFromVega, 'star-7023', 'HD 200775', ['NGC 7023'], 'NGC 7023');
    expect(plan.kind === 'focus' && plan.handoff.targetId).toBe('NGC 7023');
  });

  it('opens the same star on another target when the person switched to it', () => {
    const plan = planBackToImage(savedFromVega, 'vega-id', 'Vega', ['M 31', 'M 110'], 'M 110');
    expect(plan.kind === 'focus' && plan.handoff.targetId).toBe('M 110');
  });

  it('prefers the target selected in the list, then the star\'s first target', () => {
    expect(targetForStar(['M 31', 'M 110'], 'M 110')).toBe('M 110');
    expect(targetForStar(['M 31', 'M 110'], 'M 81')).toBe('M 31');
    expect(targetForStar([], '')).toBeNull();
  });
});

describe('StarSummaryCard Back to image after switching stars', () => {
  beforeEach(() => {
    clearImageView();
    window.localStorage.clear();
  });

  it('hands the star and its current target to Image Processing', () => {
    saveImageView(savedFromVega);
    render(
      <StarSummaryCard
        starId="star-7023"
        selectedTargetId="NGC 7023"
        astronomyData={{ id: 'star-7023', name: 'HD 200775', targetIds: ['NGC 7023'] }}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Back to image' }));
    const handoffs = peekHandoffs('Image Processing', 'targetSelected');
    expect(handoffs[handoffs.length - 1]?.payload).toEqual({
      targetId: 'NGC 7023', enableAstrometry: true, focusLibraryStarId: 'star-7023', focusStarName: 'HD 200775',
    });
    expect(peekHandoffs('Image Processing', 'returnToImage')).toHaveLength(0);
  });
});
