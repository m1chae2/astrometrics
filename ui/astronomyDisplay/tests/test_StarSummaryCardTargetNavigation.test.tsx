/**
 * @file test_StarSummaryCardTargetNavigation.test.tsx
 * @description Unit tests for StarSummaryCard target badge navigation.
 * Verifies that clicking an associated target badge dispatches the target selection event,
 * switches application mode to 'Image Processing', and updates TargetContext.
 */

import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StarSummaryCard } from '../components/StarSummaryCard';
import { TargetProvider, useTargetContext } from '../../common/context/TargetContext';

/**
 * Test helper component that displays current TargetContext values alongside StarSummaryCard.
 */
const TargetContextWatcher: React.FC<{ astronomyData: any }> = ({ astronomyData }) => {
  const { selectedTarget, pendingTarget } = useTargetContext();
  return (
    <div>
      <div data-testid="selected-target-value">{selectedTarget}</div>
      <div data-testid="pending-target-value">{pendingTarget}</div>
      <StarSummaryCard astronomyData={astronomyData} starId="test-star" />
    </div>
  );
};

describe('StarSummaryCard Target Navigation', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * Verifies that target tags render as accessible buttons with proper titles.
   */
  it('renders associated target identifiers as interactive buttons', () => {
    const astronomyData = {
      id: 'CI* NGC 6205 KAD 656',
      name: 'CI* NGC 6205 KAD 656',
      targetIds: ['M 13', 'NGC 6205'],
    };

    render(
      <TargetProvider>
        <StarSummaryCard astronomyData={astronomyData} starId="test-star" />
      </TargetProvider>
    );

    const m13Button = screen.getByRole('button', { name: 'M 13' });
    const ngcButton = screen.getByRole('button', { name: 'NGC 6205' });

    expect(m13Button).toBeInTheDocument();
    expect(m13Button).toHaveAttribute('title', 'Open M 13 in Image Processing Display with Astrometry enabled');
    expect(ngcButton).toBeInTheDocument();
    expect(ngcButton).toHaveAttribute('title', 'Open NGC 6205 in Image Processing Display with Astrometry enabled');
  });

  /**
   * Verifies that clicking an associated target button dispatches mode change and target selection events.
   */
  it('dispatches targetSelected and modeChange events when a target button is clicked', () => {
    const astronomyData = {
      id: 'CI* NGC 6205 KAD 656',
      name: 'CI* NGC 6205 KAD 656',
      targetIds: ['M 13'],
    };

    const targetSelectedSpy = vi.fn();
    const modeChangeSpy = vi.fn();

    window.addEventListener('astrometrics:targetSelected', targetSelectedSpy);
    window.addEventListener('astrometrics:modeChange', modeChangeSpy);

    render(
      <TargetProvider>
        <TargetContextWatcher astronomyData={astronomyData} />
      </TargetProvider>
    );

    const m13Button = screen.getByRole('button', { name: 'M 13' });
    fireEvent.click(m13Button);

    // Verify events dispatched
    expect(targetSelectedSpy).toHaveBeenCalled();
    const targetEvent = targetSelectedSpy.mock.calls[0][0] as CustomEvent;
    expect(targetEvent.detail).toEqual({
      targetId: 'M 13',
      starId: 'CI* NGC 6205 KAD 656',
      enableAstrometry: true,
    });

    expect(modeChangeSpy).toHaveBeenCalled();
    const modeEvent = modeChangeSpy.mock.calls[0][0] as CustomEvent;
    expect(modeEvent.detail).toBe('Image Processing');

    // Verify localStorage updated
    expect(window.localStorage.getItem('selectedTarget')).toBe('M 13');
    expect(window.localStorage.getItem('appMode')).toBe('Image Processing');
    expect(window.localStorage.getItem('astrometrics:enableAstrometryOverlay')).toBe('true');
    expect(window.localStorage.getItem('astrometrics:imageProcessingSelectedStar')).toBe('CI* NGC 6205 KAD 656');

    // Verify TargetContext updated
    expect(screen.getByTestId('selected-target-value').textContent).toBe('M 13');
    expect(screen.getByTestId('pending-target-value').textContent).toBe('M 13');

    window.removeEventListener('astrometrics:targetSelected', targetSelectedSpy);
    window.removeEventListener('astrometrics:modeChange', modeChangeSpy);
  });
});
