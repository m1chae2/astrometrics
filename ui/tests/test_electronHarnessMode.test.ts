/**
 * @fileoverview Unit tests for electron/harness_mode.js, the options that apply only when the TPM harness starts the
 * app. Checks that a normal run is unchanged and that a harness run turns off page throttling and shows the window
 * without taking focus.
 */

import { describe, expect, it, vi } from 'vitest';
import { harnessWebPreferences, harnessWindowOptions, isHarnessRun, showMainWindow } from '../../electron/harness_mode.js';

describe('harness_mode', () => {
  it('is a harness run only when ASTROMETRICS_TPM_HARNESS is 1', () => {
    expect(isHarnessRun({ ASTROMETRICS_TPM_HARNESS: '1' })).toBe(true);
    expect(isHarnessRun({ ASTROMETRICS_TPM_HARNESS: '0' })).toBe(false);
    expect(isHarnessRun({})).toBe(false);
  });

  it('adds no web preferences in a normal run', () => {
    expect(harnessWebPreferences({})).toEqual({});
  });

  it('turns off background throttling in a harness run', () => {
    expect(harnessWebPreferences({ ASTROMETRICS_TPM_HARNESS: '1' })).toEqual({ backgroundThrottling: false });
  });

  it('shows the window with focus in a normal run and without focus in a harness run', () => {
    const normal = { show: vi.fn(), showInactive: vi.fn() };
    showMainWindow(normal, {});
    expect(normal.show).toHaveBeenCalledTimes(1);
    expect(normal.showInactive).not.toHaveBeenCalled();

    const harness = { show: vi.fn(), showInactive: vi.fn() };
    showMainWindow(harness, { ASTROMETRICS_TPM_HARNESS: '1' });
    expect(harness.showInactive).toHaveBeenCalledTimes(1);
    expect(harness.show).not.toHaveBeenCalled();
  });

  it('sets a window size only in a harness run that asks for one', () => {
    expect(harnessWindowOptions({ ASTROMETRICS_TPM_WINDOW_SIZE: '1920x1080' })).toEqual({});
    expect(harnessWindowOptions({ ASTROMETRICS_TPM_HARNESS: '1' })).toEqual({});
    expect(harnessWindowOptions({ ASTROMETRICS_TPM_HARNESS: '1', ASTROMETRICS_TPM_WINDOW_SIZE: 'wide' })).toEqual({});
    expect(harnessWindowOptions({ ASTROMETRICS_TPM_HARNESS: '1', ASTROMETRICS_TPM_WINDOW_SIZE: '1920x1080' })).toEqual({
      width: 1920,
      height: 1080,
    });
  });
});
