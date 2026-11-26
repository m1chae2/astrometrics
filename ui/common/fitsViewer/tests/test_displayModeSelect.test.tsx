/**
 * @file test_displayModeSelect.test.tsx
 * @description Tests the Display control: its two choices, their tooltips, and switching between them.
 */
import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DisplayModeSelect } from '../DisplayModeSelect';
import { DEFAULT_DISPLAY_MODE } from '../displayMode';
import type { DisplayMode } from '../displayMode';

/** Holds the mode in state so a pick changes what the control shows. */
const Harness: React.FC<{ onChangeSpy?: (mode: DisplayMode) => void }> = ({ onChangeSpy }) => {
  const [mode, setMode] = useState<DisplayMode>(DEFAULT_DISPLAY_MODE);
  return <DisplayModeSelect mode={mode} onChange={(next) => { onChangeSpy?.(next); setMode(next); }} />;
};

describe('DisplayModeSelect', () => {
  it('starts as Auto Stretch', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: 'Display: Auto Stretch' })).toBeTruthy();
  });

  it('offers Linear and Auto Stretch with their tooltips, and no minimum-to-maximum view', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Display: Auto Stretch' }));
    const linear = screen.getByRole('button', { name: 'Linear' });
    const autoStretch = screen.getAllByRole('button', { name: 'Auto Stretch' })[0];
    expect(linear.getAttribute('title')).toBe('Brightness shown in proportion, with the range set from the sky noise, so equal noise looks equally grainy on any image. Use this to judge star sizes, halos and pairs.');
    expect(autoStretch.getAttribute('title')).toBe('Brightens faint detail. Star sizes and halos look larger than they are.');
    expect(screen.queryByText(/min|max/i)).toBeNull();
  });

  it('switches mode and reports it', () => {
    const picked: DisplayMode[] = [];
    render(<Harness onChangeSpy={(mode) => picked.push(mode)} />);
    fireEvent.click(screen.getByRole('button', { name: 'Display: Auto Stretch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Linear' }));
    expect(picked).toEqual(['linear']);
    expect(screen.getByRole('button', { name: 'Display: Linear' })).toBeTruthy();
  });
});
