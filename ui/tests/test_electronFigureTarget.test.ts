/**
 * @fileoverview Unit tests for electron/figure_target.js. A figure window may
 * show only the backend's own /figure/<number> page, or a PNG file inside the
 * backend's scratch folder. These tests check that everything else is refused.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { resolveFigureTarget, resolveFigureUrl, resolvePlotPath } from '../../electron/figure_target.js';

const BASE = 'http://127.0.0.1:5000';

describe('resolveFigureUrl', () => {
  /** A relative or full figure page address on the backend is accepted. */
  it('accepts the backend figure page', () => {
    expect(resolveFigureUrl('/figure/3', BASE)).toBe('http://127.0.0.1:5000/figure/3');
    expect(resolveFigureUrl('http://127.0.0.1:5000/figure/12', BASE)).toBe('http://127.0.0.1:5000/figure/12');
  });

  /** Other sites, other ports, other paths and extra parts are refused. */
  it('refuses every other address', () => {
    for (const bad of [
      'https://example.com/figure/1',
      'http://127.0.0.1:5001/figure/1',
      'http://localhost:5000/figure/1',
      '//example.com/figure/1',
      '/api/rpc',
      '/figure/mpl.js',
      '/figure/1/download.png',
      '/figure/1/../../api/rpc',
      '/figure/1?x=1',
      '/figure/1#x',
      'file:///etc/passwd',
      'javascript:alert(1)',
      '',
      42,
    ]) {
      expect(resolveFigureUrl(bad, BASE)).toBeNull();
    }
  });
});

describe('resolvePlotPath', () => {
  let root: string;
  let scratch: string;
  let outside: string;

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'figure-target-'));
    scratch = path.join(root, 'astrometrics');
    outside = path.join(root, 'outside');
    fs.mkdirSync(scratch);
    fs.mkdirSync(outside);
    fs.writeFileSync(path.join(scratch, 'plot.png'), 'png');
    fs.writeFileSync(path.join(scratch, 'notes.txt'), 'txt');
    fs.writeFileSync(path.join(outside, 'secret.png'), 'png');
  });

  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  /** A PNG file inside the scratch folder is accepted. */
  it('accepts a PNG in the scratch folder', () => {
    const file = path.join(scratch, 'plot.png');
    expect(resolvePlotPath(file, scratch)).toBe(fs.realpathSync(file));
  });

  /** Files outside the folder, other file types, and missing files are refused. */
  it('refuses other files', () => {
    expect(resolvePlotPath(path.join(outside, 'secret.png'), scratch)).toBeNull();
    expect(resolvePlotPath(path.join(scratch, '..', 'outside', 'secret.png'), scratch)).toBeNull();
    expect(resolvePlotPath(path.join(scratch, 'notes.txt'), scratch)).toBeNull();
    expect(resolvePlotPath(path.join(scratch, 'missing.png'), scratch)).toBeNull();
    expect(resolvePlotPath('plot.png', scratch)).toBeNull();
  });

  /** A link inside the folder that points outside it is refused. */
  it('refuses a link that leads out of the folder', () => {
    const link = path.join(scratch, 'link.png');
    fs.symlinkSync(path.join(outside, 'secret.png'), link);
    expect(resolvePlotPath(link, scratch)).toBeNull();
  });

  /** resolveFigureTarget sorts a request and reports each refused part. */
  it('sorts a request and reports refused parts', () => {
    const file = path.join(scratch, 'plot.png');
    const options = { baseUrl: BASE, directory: scratch };

    expect(resolveFigureTarget({ interactiveUrl: '/figure/2', plotPath: file }, options)).toEqual({
      interactiveUrl: 'http://127.0.0.1:5000/figure/2',
      plotPath: fs.realpathSync(file),
      rejected: [],
    });
    expect(resolveFigureTarget(file, options).plotPath).toBe(fs.realpathSync(file));

    const refused = resolveFigureTarget({ interactiveUrl: 'https://example.com/', plotPath: '/etc/passwd' }, options);
    expect(refused.interactiveUrl).toBeNull();
    expect(refused.plotPath).toBeNull();
    expect(refused.rejected).toHaveLength(2);
  });
});
