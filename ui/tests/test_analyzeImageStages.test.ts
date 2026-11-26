/**
 * @fileoverview Tests that the analysis service sends the picked stages to the backend, and leaves
 * the field out when no stages are picked.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import { analyzeImage, analyzeTarget } from '../common/services/imaging/processingService';

describe('analyzeImage stages', () => {
  beforeEach(() => {
    vi.mocked(callBackend).mockReset();
    vi.mocked(callBackend).mockResolvedValue({ status: 'started' } as never);
  });

  it('sends the stages when they are given', async () => {
    await analyzeTarget('M 57', ['/a.fits'], 'L', ['photometry']);
    expect(callBackend).toHaveBeenCalledWith('analysis:analyze_image', {
      target_id: 'M 57',
      image_files: ['/a.fits'],
      filter_name: 'L',
      type: 'photometry',
      stages: ['photometry'],
    });
  });

  it('leaves the stages out when none are given or the list is empty', async () => {
    await analyzeImage('M 57', ['/a.fits']);
    await analyzeImage('M 57', ['/a.fits'], undefined, 'photometry', []);
    for (const call of vi.mocked(callBackend).mock.calls) {
      expect(call[1]).not.toHaveProperty('stages');
    }
  });
});
