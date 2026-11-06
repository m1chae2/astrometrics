/**
 * @fileoverview Tests for the status header's telemetry strip used as navigation.
 * The strip opens the Observatory Manager. Inside the Observatory Manager only the
 * Target item is clickable, and it opens Image Processing with that target.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StatusHeader } from '../StatusHeader';

const chooseMode = vi.fn();
const setSelectedTarget = vi.fn();
const setPendingTarget = vi.fn();
let selectedMode = 'Image Processing';
let targetName: string | undefined = 'M 31';

vi.mock('../hooks/useStatusData', () => ({
  useStatusData: () => ({
    telemetry: { altitude: '-', azimuth: '-', temperature: '-', humidity: '-', ra: '-', dec: '-' },
    connected: true,
    trackingStatus: 'Tracking',
    telescopeConnection: true,
    selectedMode,
    chooseMode,
  }),
}));

vi.mock('../hooks/useSettingsModal', () => ({
  useSettingsModal: () => ({ open: false, closing: false, handleOpen: vi.fn(), handleClose: vi.fn() }),
}));

vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ config: {}, telescope: { targetName } }),
}));

vi.mock('../../common/context/TargetContext', () => ({
  useOptionalTargetContext: () => ({ setSelectedTarget, setPendingTarget }),
}));

vi.mock('../SettingsPanel', () => ({
  SettingsPanel: () => <div data-testid="settings-panel" />,
}));

describe('StatusHeader telemetry navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    selectedMode = 'Image Processing';
    targetName = 'M 31';
  });

  /** Clicking the strip outside the Observatory Manager opens the Observatory Manager. */
  it('opens the Observatory Manager when the strip is clicked', () => {
    render(<StatusHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Observatory Manager' }));
    expect(chooseMode).toHaveBeenCalledWith('Observatory Manager');
  });

  /** The tooltip sits on the clickable strip, where the mouse can reach it. */
  it('puts the strip tooltip on the clickable strip, not the hidden button', () => {
    const { container } = render(<StatusHeader />);
    const strip = container.querySelector('.header__right--clickable') as HTMLElement;
    expect(strip.getAttribute('title')).toBe('Open Observatory Manager');
    const button = screen.getByRole('button', { name: 'Open Observatory Manager' });
    expect(button.getAttribute('title')).toBeNull();
  });

  /** In Image Processing the Target item is part of the strip, not a separate button. */
  it('has no separate Target button outside the Observatory Manager', () => {
    render(<StatusHeader />);
    expect(screen.queryByRole('button', { name: /in Image Processing/ })).toBeNull();
  });

  /** In the Observatory Manager the Target item opens Image Processing on that target. */
  it('opens the target in Image Processing from the Observatory Manager', () => {
    selectedMode = 'Observatory Manager';
    render(<StatusHeader />);
    fireEvent.click(screen.getByRole('button', { name: 'Open M 31 in Image Processing' }));
    expect(setSelectedTarget).toHaveBeenCalledWith('M 31');
    expect(setPendingTarget).toHaveBeenCalledWith('M 31');
    expect(chooseMode).toHaveBeenCalledWith('Image Processing');
  });

  /** In the Observatory Manager the rest of the strip is not clickable. */
  it('does nothing elsewhere on the strip in the Observatory Manager', () => {
    selectedMode = 'Observatory Manager';
    render(<StatusHeader />);
    expect(screen.queryByRole('button', { name: 'Open Observatory Manager' })).toBeNull();
    expect(chooseMode).not.toHaveBeenCalled();
  });

  /** The Enter key on the focused button does the same as a click. */
  it('responds to the Enter key', async () => {
    selectedMode = 'Observatory Manager';
    render(<StatusHeader />);
    screen.getByRole('button', { name: 'Open M 31 in Image Processing' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(chooseMode).toHaveBeenCalledWith('Image Processing');
  });

  /** Without a known target there is no Target button. */
  it('has no Target button when no target is known', () => {
    selectedMode = 'Observatory Manager';
    targetName = undefined;
    render(<StatusHeader />);
    expect(screen.queryByRole('button', { name: /in Image Processing/ })).toBeNull();
  });
});
