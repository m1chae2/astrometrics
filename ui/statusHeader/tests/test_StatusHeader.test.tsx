/**
 * @fileoverview Unit tests for the streamlined StatusHeader component.
 * Verifies rendering of the integrated single header bar with flat inline telemetry
 * (coordinates, weather, mount tracking) and navigation controls.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusHeader } from '../StatusHeader';

// Mock dependencies
vi.mock('../hooks/useStatusData', () => ({
  useStatusData: vi.fn(),
}));

vi.mock('../hooks/useSettingsModal', () => ({
  useSettingsModal: vi.fn(() => ({
    open: false,
    closing: false,
    handleOpen: vi.fn(),
    handleClose: vi.fn(),
  })),
}));

vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: vi.fn(() => ({
    config: {},
  })),
}));

vi.mock('../SettingsPanel', () => ({
  SettingsPanel: () => <div data-testid="settings-panel" />,
}));

import { useStatusData } from '../hooks/useStatusData';

describe('StatusHeader component', () => {
  /**
   * Test rendering when telemetry has active tracking and coordinates.
   */
  it('renders flat inline telemetry in single header bar when connected', () => {
    vi.mocked(useStatusData).mockReturnValue({
      telemetry: {
        altitude: "45° 12' 30\"",
        azimuth: "180° 30' 46\"",
        temperature: '18',
        humidity: '42',
        ra: '10h 00m 03s',
        dec: "+20° 00' 00\"",
      },
      connected: true,
      trackingStatus: 'Tracking',
      telescopeConnection: true,
      selectedMode: 'Astronomy Manager',
      chooseMode: vi.fn(),
    });

    render(<StatusHeader />);

    // Navigation
    expect(screen.getByText('Astronomy Manager')).toBeTruthy();

    // Flat telemetry items
    expect(screen.getByText('Tracking')).toBeTruthy();
    expect(screen.getByText('RA')).toBeTruthy();
    expect(screen.getByText('10h 00m 03s')).toBeTruthy();
    expect(screen.getByText('DEC')).toBeTruthy();
    expect(screen.getByText("+20° 00' 00\"")).toBeTruthy();
    expect(screen.getByText('ALT')).toBeTruthy();
    expect(screen.getByText("45° 12' 30\"")).toBeTruthy();
    expect(screen.getByText('AZ')).toBeTruthy();
    expect(screen.getByText("180° 30' 46\"")).toBeTruthy();
    expect(screen.getByText('18')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
  });

  /**
   * Test rendering when telescope is disconnected.
   */
  it('renders disconnected state gracefully', () => {
    vi.mocked(useStatusData).mockReturnValue({
      telemetry: {
        altitude: '-',
        azimuth: '-',
        temperature: '-',
        humidity: '-',
        ra: '-',
        dec: '-',
      },
      connected: false,
      trackingStatus: 'Not Tracking',
      telescopeConnection: false,
      selectedMode: 'Image Viewer',
      chooseMode: vi.fn(),
    });

    render(<StatusHeader />);

    expect(screen.getByText('Image Viewer')).toBeTruthy();
    expect(screen.getByText('Not Tracking')).toBeTruthy();
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThanOrEqual(4);
  });
});
