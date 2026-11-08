/**
 * @file test_captureService.test.ts
 * @description Tests of the open-ended capture: it sends the fixed settings (60 s, luminance, LIGHT, a large
 * frame count) through `imaging:capture`, and Stop Capture calls `imaging:stop_capture`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { callBackendMock } = vi.hoisted(() => ({ callBackendMock: vi.fn() }));
vi.mock('../services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));
vi.mock('../utils/reportError', () => ({ reportError: vi.fn() }));

import {
  OPEN_ENDED_CAPTURE_FRAME_COUNT,
  startOpenEndedCapture,
  stopCapture,
} from '../services/imaging/captureService';

describe('captureService open-ended capture', () => {
  beforeEach(() => { callBackendMock.mockReset(); });

  it('starts 60 s luminance light frames with the backstop count', async () => {
    callBackendMock.mockResolvedValue('job-7');
    const jobId = await startOpenEndedCapture('M 81');
    expect(jobId).toBe('job-7');
    expect(callBackendMock).toHaveBeenCalledWith('imaging:capture', {
      target_id: 'M 81',
      exposure_seconds: 60,
      count: OPEN_ENDED_CAPTURE_FRAME_COUNT,
      image_type: 'LIGHT',
      filter_name: 'L',
      delay_seconds: undefined,
    });
  });

  it('returns null when the capture cannot start', async () => {
    callBackendMock.mockImplementation(async () => { throw new Error('no camera'); });
    expect(await startOpenEndedCapture('M 81')).toBeNull();
  });

  it('stops through imaging:stop_capture', async () => {
    callBackendMock.mockResolvedValue(true);
    expect(await stopCapture()).toBe(true);
    expect(callBackendMock).toHaveBeenCalledWith('imaging:stop_capture', {});
  });

  it('reports false when the stop fails', async () => {
    callBackendMock.mockImplementation(async () => { throw new Error('refused'); });
    expect(await stopCapture()).toBe(false);
  });
});
