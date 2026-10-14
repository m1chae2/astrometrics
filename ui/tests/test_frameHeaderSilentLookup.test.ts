/**
 * @fileoverview Tests that an optional frame-header lookup fails quietly.
 * The Image Viewer and Image Processing read a stack's header only to show
 * its exposure time and fall back to the catalog value when it cannot be read
 * (for example when the file is missing from disk). That failure must not
 * show an error toast or an error report on every target load.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn() }));
vi.mock('../common/utils/reportError', () => ({ reportError: vi.fn() }));
vi.mock('../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import { reportError } from '../common/utils/reportError';
import { fetchTargetFrameHeader } from '../common/services/targetService';

describe('fetchTargetFrameHeader', () => {
  beforeEach(() => {
    vi.mocked(callBackend).mockReset();
    vi.mocked(reportError).mockReset();
  });

  it('asks the backend not to toast and does not report when silent', async () => {
    vi.mocked(callBackend).mockRejectedValue(new Error('File not found: /x.fits'));

    await expect(fetchTargetFrameHeader('C 2022 E3 ZTF', '/x.fits', { silent: true })).rejects.toThrow('File not found');

    expect(callBackend).toHaveBeenCalledWith('target:get_frame_header', expect.anything(), { silent: true });
    expect(reportError).not.toHaveBeenCalled();
  });

  it('still reports the failure when the user asked for the header', async () => {
    vi.mocked(callBackend).mockRejectedValue(new Error('File not found: /x.fits'));

    await expect(fetchTargetFrameHeader('M_81', '/x.fits')).rejects.toThrow('File not found');

    expect(reportError).toHaveBeenCalledTimes(1);
  });
});
