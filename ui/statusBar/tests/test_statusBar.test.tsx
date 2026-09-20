/**
 * @fileoverview Unit tests for the workstation StatusBar component.
 * Verifies rendering of coordinates, environmental sensors, and connection indicators.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBar } from '../StatusBar';

// Mock useAstrometrics context hook
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: vi.fn(),
}));

import { useAstrometrics } from '../../common/context/AstrometricsContext';

describe('StatusBar component', () => {
  /**
   * Test rendering when telemetry has full values.
   */
  it('renders telescope coordinates and telemetry values accurately', () => {
    vi.mocked(useAstrometrics).mockReturnValue({
      telescope: {
        ra: '10h 00m 03.42s',
        dec: "+20° 00' 15.60\"",
        altitude: "45° 12' 30.12\"",
        azimuth: "180° 30' 45.80\"",
        temperature: '18.4 °C',
        humidity: '42.2 %',
        connectionStatus: 'Connected',
        trackingStatus: 'Tracking',
      },
      connected: true,
      config: {},
      isInitialized: true,
    } as any);

    render(<StatusBar />);

    expect(screen.getByText('Tracking')).toBeTruthy();
    expect(screen.getByText('RA')).toBeTruthy();
    expect(screen.getByText('10h 00m 03.42s')).toBeTruthy();
    expect(screen.getByText('DEC')).toBeTruthy();
    expect(screen.getByText("+20° 00' 16\"")).toBeTruthy();
    expect(screen.getByText('ALT')).toBeTruthy();
    expect(screen.getByText("45° 12' 30\"")).toBeTruthy();
    expect(screen.getByText('AZ')).toBeTruthy();
    expect(screen.getByText("180° 30' 46\"")).toBeTruthy();
    expect(screen.getByText('18')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
    expect(screen.getByText('ONLINE')).toBeTruthy();
  });

  /**
   * Test rendering when telemetry is disconnected or values are empty.
   */
  it('renders dashes and offline status gracefully when disconnected', () => {
    vi.mocked(useAstrometrics).mockReturnValue({
      telescope: {
        ra: '',
        dec: '',
        altitude: '',
        azimuth: '',
        temperature: '',
        humidity: '',
        connectionStatus: 'Disconnected',
        trackingStatus: '',
      },
      connected: false,
      config: {},
      isInitialized: true,
    } as any);

    render(<StatusBar />);

    expect(screen.getByText('Not Tracking')).toBeTruthy();
    expect(screen.getByText('OFFLINE')).toBeTruthy();
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThanOrEqual(4);
  });
});
