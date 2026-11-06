/**
 * @fileoverview Tests that `resolveImageSrc` maps paths to URLs with the
 * backend's table of served folders and never guesses from folder names.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { resolveImageSrc, setStaticMountsForTesting } from '../common/services/backendApi';

describe('resolveImageSrc', () => {
  afterEach(() => setStaticMountsForTesting(null));

  it('maps the sandbox layout (stacks inside a library folder) to the frames mount', () => {
    setStaticMountsForTesting([
      { root: '/tmp/astrometrics-tpm-1/library', urlPrefix: '/static' },
      { root: '/tmp/astrometrics-tpm-1/library/frames', urlPrefix: '/static/frames' },
      { root: '/tmp/astrometrics-tpm-1/library/stacks', urlPrefix: '/static/frames' },
    ]);
    const url = resolveImageSrc('/tmp/astrometrics-tpm-1/library/stacks/lights/M 31/M_31_preview.jpg');
    expect(url).toMatch(/\/static\/frames\/lights\/M%2031\/M_31_preview\.jpg$/);
    expect(url).not.toContain('/static/library/');
  });

  it('maps frames, stacks and library on different disks', () => {
    setStaticMountsForTesting([
      { root: '/data/library', urlPrefix: '/static' },
      { root: '/mnt/win/frames', urlPrefix: '/static/frames' },
      { root: '/home/u/stacks', urlPrefix: '/static/frames' },
    ]);
    expect(resolveImageSrc('/home/u/stacks/lights/M 57/a_processed.fits')).toMatch(
      /\/static\/frames\/lights\/M%2057\/a_processed\.fits$/
    );
    expect(resolveImageSrc('/mnt/win/frames/lights/M 57/b.fits')).toMatch(/\/static\/frames\/lights\/M%2057\/b\.fits$/);
    expect(resolveImageSrc('/data/library/x.png')).toMatch(/\/static\/x\.png$/);
  });

  it('returns an empty string for a path no mount serves or before the table loads', () => {
    expect(resolveImageSrc('/data/library/lights/x.jpg')).toBe('');
    setStaticMountsForTesting([{ root: '/data/library', urlPrefix: '/static' }]);
    expect(resolveImageSrc('/data/library2/lights/x.jpg')).toBe('');
  });
});
