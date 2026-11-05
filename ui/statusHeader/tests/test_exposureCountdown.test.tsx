/**
 * @fileoverview Tests for the camera exposure countdown in the status header:
 * it must not step back when a rounded-up server value arrives, but it must
 * follow the server when the server clearly disagrees or a new frame starts.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { StatusHeader } from '../StatusHeader';

vi.mock('../hooks/useStatusData', () => ({ useStatusData: vi.fn() }));
vi.mock('../hooks/useSettingsModal', () => ({
  useSettingsModal: vi.fn(() => ({ open: false, closing: false, handleOpen: vi.fn(), handleClose: vi.fn() })),
}));
vi.mock('../SettingsPanel', () => ({ SettingsPanel: () => <div /> }));
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: vi.fn(() => ({ config: {}, telescope: {} })),
}));

import { useStatusData } from '../hooks/useStatusData';

/**
 * Makes the mocked status data report the given camera status.
 *
 * @param cameraStatus - Text such as "Exposing (57s)".
 */
function mockCameraStatus(cameraStatus: string): void {
  vi.mocked(useStatusData).mockReturnValue({
    telemetry: { altitude: '-', azimuth: '-', temperature: '-', humidity: '-', ra: '-', dec: '-', cameraStatus },
    connected: true,
    trackingStatus: 'Tracking',
    telescopeConnection: true,
    selectedMode: 'Image Viewer',
    chooseMode: vi.fn(),
  });
}

/**
 * Reads the number of seconds currently shown in the header.
 *
 * @returns The displayed seconds, or null if no countdown is shown.
 */
function shownSeconds(): number | null {
  const match = document.body.textContent?.match(/Exposing \((\d+)s\)/);
  return match ? Number(match[1]) : null;
}

describe('StatusHeader exposure countdown', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Starts a countdown at 60 s and lets 4.6 s pass, leaving about 55.4 s. */
  function startAndRunTo55(): ReturnType<typeof render> {
    mockCameraStatus('Exposing (60s)');
    const view = render(<StatusHeader />);
    act(() => { vi.advanceTimersByTime(4600); });
    return view;
  }

  /** A slightly higher rounded-up server value must not raise the display. */
  it('does not step back up when the server value is a little higher', () => {
    const view = startAndRunTo55();
    const before = shownSeconds() as number;
    expect(before).toBe(56);
    mockCameraStatus('Exposing (57s)');
    view.rerender(<StatusHeader />);
    expect(shownSeconds()).toBeLessThanOrEqual(before);
  });

  /** A much higher server value means a new frame, so the display resets. */
  it('resets when the server value is far higher (new frame)', () => {
    mockCameraStatus('Exposing (60s)');
    const view = render(<StatusHeader />);
    act(() => { vi.advanceTimersByTime(58000); });
    expect(shownSeconds()).toBe(2);
    mockCameraStatus('Exposing (3s)');
    view.rerender(<StatusHeader />);
    expect(shownSeconds()).toBeLessThanOrEqual(2);
    mockCameraStatus('Exposing (60s)');
    view.rerender(<StatusHeader />);
    expect(shownSeconds()).toBe(60);
  });

  /** A server value clearly lower than the local one snaps the display down. */
  it('snaps down when the server value is much lower', () => {
    const view = startAndRunTo55();
    mockCameraStatus('Exposing (50s)');
    view.rerender(<StatusHeader />);
    expect(shownSeconds()).toBe(50);
  });
});
