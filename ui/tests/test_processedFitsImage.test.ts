/**
 * @fileoverview Unit tests for loading a target's stretched processed FITS picture.
 *
 * Verifies that:
 * 1. Only the pipeline's `<stack>_processed.fits` files are treated as stretched pictures.
 * 2. Such a file is fetched as raw bytes and typed as FITS, not converted to a PNG on the server.
 * 3. A processed FITS that is missing on the server gives no image instead of an error.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({
  callBackend: vi.fn(),
  resolveImageSrc: (path: string) => `http://backend/static/frames${path}`,
  fetchImageFile: (url: string, signal?: AbortSignal) => fetch(url, { method: 'GET', signal }),
}));
vi.mock('../common/utils/reportError', () => ({ reportError: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import {
  FITS_BLOB_TYPE,
  fetchProcessedImage,
  isProcessedFitsPath,
} from '../common/services/imaging/imageService';

const PROCESSED = '/lights/M 57/M_57_L_Stacked_processed.fits';

describe('processed FITS pictures', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.mocked(callBackend).mockReset();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('recognises only the stretched pictures the pipeline saves', () => {
    expect(isProcessedFitsPath(PROCESSED)).toBe(true);
    expect(isProcessedFitsPath('/x/Y_PROCESSED.FIT')).toBe(true);
    expect(isProcessedFitsPath('/lights/M 57/M_57_L_Stacked.fits')).toBe(false);
    expect(isProcessedFitsPath('/x/Y_processed.jpg')).toBe(false);
  });

  it('fetches the file as bytes and types it as FITS', async () => {
    vi.mocked(callBackend).mockResolvedValue({ stacking: { processedImage: PROCESSED } } as never);
    global.fetch = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'Content-Type': 'image/fits' } })
    );

    const blob = await fetchProcessedImage('M 57');

    expect(blob?.type).toBe(FITS_BLOB_TYPE);
    expect(blob?.size).toBe(3);
    expect(vi.mocked(callBackend)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(callBackend)).not.toHaveBeenCalledWith('images:convert_fits', expect.anything());
  });

  it('returns no image when the file is missing', async () => {
    vi.mocked(callBackend).mockResolvedValue({ stacking: { processedImage: PROCESSED } } as never);
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));

    expect(await fetchProcessedImage('M 57')).toBeNull();
  });
});
