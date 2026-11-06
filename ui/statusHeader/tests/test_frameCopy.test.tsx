/**
 * @fileoverview Tests for the frame copy text next to the camera state in the
 * status header and the "Reading out" name for the camera's own download.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusHeader } from '../StatusHeader';

vi.mock('../hooks/useStatusData', () => ({ useStatusData: vi.fn() }));
vi.mock('../hooks/useSettingsModal', () => ({
  useSettingsModal: vi.fn(() => ({ open: false, closing: false, handleOpen: vi.fn(), handleClose: vi.fn() })),
}));
vi.mock('../ObservatorySourceChip', () => ({ ObservatorySourceChip: () => null }));

vi.mock('../SettingsPanel', () => ({ SettingsPanel: () => <div /> }));
vi.mock('../../common/context/AstrometricsContext', () => ({ useAstrometrics: vi.fn() }));

import { useStatusData } from '../hooks/useStatusData';
import { useAstrometrics } from '../../common/context/AstrometricsContext';

/**
 * Makes the mocked data report a camera status and a copy progress.
 *
 * @param cameraStatus - The backend camera status text.
 * @param frameCopy - The copy progress from the telescope status, or undefined.
 */
function mockHeader(cameraStatus: string, frameCopy?: Record<string, unknown>): void {
  vi.mocked(useStatusData).mockReturnValue({
    telemetry: { altitude: '-', azimuth: '-', temperature: '-', humidity: '-', ra: '-', dec: '-', cameraStatus },
    connected: true,
    trackingStatus: 'Tracking',
    telescopeConnection: true,
    selectedMode: 'Image Viewer',
    chooseMode: vi.fn(),
  });
  vi.mocked(useAstrometrics).mockReturnValue({
    config: {},
    telescope: { targetName: 'M 31', frameCopy },
  } as unknown as ReturnType<typeof useAstrometrics>);
}

describe('StatusHeader frame copy text', () => {
  /** Analyzing counts are shown after the camera state, with a tooltip. */
  it('shows the measured count next to the camera state', () => {
    mockHeader('Idle', { active: true, targetId: 'M 31', measured: 24, total: 40 });
    render(<StatusHeader />);
    const suffix = screen.getByTestId('frame-copy');
    expect(suffix.textContent).toContain('· Analyzing 24/40');
    expect(suffix.getAttribute('title')).toBe('Measuring the new M 31 frames: 24 of 40 done');
    expect(screen.getByText('Idle')).toBeTruthy();
  });

  /** Before the count is known the word Downloading is shown. */
  it('shows Downloading when the count is not known', () => {
    mockHeader('Idle', { active: true, targetId: 'M 31', measured: null, total: null });
    render(<StatusHeader />);
    const suffix = screen.getByTestId('frame-copy');
    expect(suffix.textContent).toContain('· Downloading');
    expect(suffix.textContent).not.toMatch(/\d\/\d/);
    expect(suffix.getAttribute('title')).toBe('Copying new M 31 frames to this computer');
  });

  /** Measuring has started but has not posted a count yet. */
  it('shows Analyzing without numbers before the job posts a count', () => {
    mockHeader('Idle', { active: true, targetId: 'M 31', measured: 0, total: null });
    render(<StatusHeader />);
    const suffix = screen.getByTestId('frame-copy');
    expect(suffix.textContent).toContain('· Analyzing');
    expect(suffix.textContent).not.toMatch(/\d\/\d/);
    expect(suffix.getAttribute('title')).toBe('Measuring the new M 31 frames');
  });

  /** A single frame still shows its count. */
  it('shows Analyzing 0/1 for a single new frame', () => {
    mockHeader('Idle', { active: true, targetId: 'M 31', measured: 0, total: 1 });
    render(<StatusHeader />);
    const suffix = screen.getByTestId('frame-copy');
    expect(suffix.textContent).toContain('· Analyzing 0/1');
    expect(suffix.getAttribute('title')).toBe('Measuring the new M 31 frames: 0 of 1 done');
  });

  /** The countdown stays while frames copy. */
  it('keeps the exposure countdown beside the copy text', () => {
    mockHeader('Exposing (42s)', { active: true, targetId: 'M 31', measured: 24, total: 40 });
    render(<StatusHeader />);
    expect(document.body.textContent).toMatch(/Exposing \(\d+s\)/);
    expect(screen.getByTestId('frame-copy').textContent).toContain('· Analyzing 24/40');
  });

  /** No copy, no text. */
  it('hides the text when no copy is running', () => {
    mockHeader('Idle', { active: false, targetId: 'M 31', measured: null, total: null });
    render(<StatusHeader />);
    expect(screen.queryByTestId('frame-copy')).toBeNull();
  });

  /** The camera's own readout is displayed as "Reading out". */
  it('shows the camera "Downloading" state as "Reading out"', () => {
    mockHeader('Downloading');
    render(<StatusHeader />);
    expect(screen.getByText('Reading out')).toBeTruthy();
    expect(screen.queryByText('Downloading')).toBeNull();
  });
});
