/**
 * @fileoverview Tests for the GUIDE item in the status header: the total RMS
 * from the newest sample, the dash for missing or stale data, and the tooltip.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusHeader } from '../StatusHeader';

vi.mock('../hooks/useStatusData', () => ({
  useStatusData: vi.fn(() => ({
    telemetry: { altitude: '-', azimuth: '-', temperature: '-', humidity: '-', ra: '-', dec: '-' },
    connected: true,
    trackingStatus: 'Tracking',
    telescopeConnection: true,
    selectedMode: 'Image Viewer',
    chooseMode: vi.fn(),
  })),
}));

vi.mock('../hooks/useSettingsModal', () => ({
  useSettingsModal: vi.fn(() => ({ open: false, closing: false, handleOpen: vi.fn(), handleClose: vi.fn() })),
}));

vi.mock('../ObservatorySourceChip', () => ({ ObservatorySourceChip: () => null }));

vi.mock('../SettingsPanel', () => ({
  SettingsPanel: () => <div data-testid="settings-panel" />,
}));

vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: vi.fn(),
}));

import { useAstrometrics } from '../../common/context/AstrometricsContext';

/** Fixed "now" used by every test, as a Unix timestamp in seconds. */
const NOW_SECONDS = 1_800_000_000;

/**
 * Makes the mocked context return a telescope with the given guiding history.
 *
 * @param guidingHistory - Samples to put in `telescope.guidingHistory`.
 */
function setGuidingHistory(guidingHistory: Record<string, unknown>[]): void {
  vi.mocked(useAstrometrics).mockReturnValue({ config: {}, telescope: { guidingHistory } } as any);
}

describe('StatusHeader guiding RMS', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW_SECONDS * 1000);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** The total comes from the newest sample, not an older one. */
  it('shows the total RMS from the newest sample', () => {
    setGuidingHistory([
      { time: NOW_SECONDS - 10, rmsRa: 1.0, rmsDec: 1.0 },
      { time: NOW_SECONDS - 1, rmsRa: 0.3, rmsDec: 0.4 },
    ]);
    render(<StatusHeader />);
    expect(screen.getByText('GUIDE')).toBeTruthy();
    expect(screen.getByTestId('guiding-rms-value').textContent).toBe('0.50″');
  });

  /** No samples means a dash. */
  it('shows a dash when there are no samples', () => {
    setGuidingHistory([]);
    render(<StatusHeader />);
    expect(screen.getByTestId('guiding-rms-value').textContent).toBe('—');
  });

  /** A sample older than 60 seconds is treated as a stopped guider. */
  it('shows a dash when the newest sample is older than 60 seconds', () => {
    setGuidingHistory([{ time: NOW_SECONDS - 61, rmsRa: 0.3, rmsDec: 0.4 }]);
    render(<StatusHeader />);
    expect(screen.getByTestId('guiding-rms-value').textContent).toBe('—');
  });

  /** The tooltip splits the total into its RA and Dec parts. */
  it('shows the RA and Dec split in the tooltip', () => {
    setGuidingHistory([{ time: NOW_SECONDS - 1, rmsRa: 0.32, rmsDec: 0.21 }]);
    render(<StatusHeader />);
    const item = screen.getByTestId('guiding-rms-value').parentElement as HTMLElement;
    expect(item.getAttribute('title')).toBe('RA 0.32″ · Dec 0.21″');
    expect(item.getAttribute('aria-label')).toContain('RA 0.32″ · Dec 0.21″');
  });
});
