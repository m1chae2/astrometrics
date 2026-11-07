/**
 * @fileoverview Unit tests for electron/app_protocol.js: the safe file lookup behind the private
 * app:// address, the file types, the request handler, and the choice between dev server and app://.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import {
  APP_INDEX_URL,
  chooseUiSource,
  createAppProtocolHandler,
  mimeTypeFor,
  resolveAppFile,
} from '../../electron/app_protocol.js';

let root: string;
let outside: string;

beforeAll(() => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'app-protocol-'));
  root = path.join(base, 'dist');
  outside = path.join(base, 'secret.txt');
  fs.mkdirSync(path.join(root, 'js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'index.html'), '<html></html>');
  fs.writeFileSync(path.join(root, 'js', 'plotly.min.js'), 'plotly');
  fs.writeFileSync(outside, 'secret');
  fs.symlinkSync(outside, path.join(root, 'link.txt'));
});

afterAll(() => {
  fs.rmSync(path.dirname(root), { recursive: true, force: true });
});

describe('resolveAppFile', () => {
  /** The root and a real file resolve to files inside the folder. */
  it('serves index.html and nested files', () => {
    expect(resolveAppFile(root, 'app://./', false).filePath).toBe(path.join(root, 'index.html'));
    expect(resolveAppFile(root, 'app://./index.html?mode=x', false).filePath).toBe(path.join(root, 'index.html'));
    expect(resolveAppFile(root, 'app://./js/plotly.min.js', false).filePath).toBe(path.join(root, 'js', 'plotly.min.js'));
  });

  /** Escape attempts are refused, plain, encoded and through a symbolic link. */
  it('refuses paths that leave the folder', () => {
    for (const bad of ['app://./../secret.txt', 'app://./%2e%2e/secret.txt', 'app://./js/..%2f..%2fsecret.txt', 'app://./a%5c..%5csecret.txt', 'app://./%00']) {
      expect(resolveAppFile(root, bad, true).filePath).toBeNull();
    }
    expect(resolveAppFile(root, 'app://./link.txt', false).filePath).toBeNull();
  });

  /** A folder reached through a symbolic link still serves its own files. */
  it('serves files when the root is a symbolic link', () => {
    const linkedRoot = path.join(path.dirname(root), 'dist-link');
    fs.symlinkSync(root, linkedRoot);
    expect(resolveAppFile(linkedRoot, 'app://./', false).status).toBe(200);
    expect(resolveAppFile(linkedRoot, 'app://./js/plotly.min.js', false).status).toBe(200);
    expect(resolveAppFile(linkedRoot, 'app://./link.txt', false).filePath).toBeNull();
  });

  /** Another scheme or host is not the app's address. */
  it('refuses other hosts and schemes', () => {
    expect(resolveAppFile(root, 'app://evil/index.html', false).status).toBe(403);
    expect(resolveAppFile(root, 'file:///etc/passwd', false).status).toBe(403);
    expect(resolveAppFile(root, 'not a url', false).status).toBe(400);
  });

  /** A missing file is 404; a missing page route falls back to index.html only for navigation. */
  it('returns 404 and falls back for navigation only', () => {
    expect(resolveAppFile(root, 'app://./js/missing.js', true)).toEqual({ filePath: null, status: 404 });
    expect(resolveAppFile(root, 'app://./missing', false)).toEqual({ filePath: null, status: 404 });
    expect(resolveAppFile(root, 'app://./some/route', true).filePath).toBe(path.join(root, 'index.html'));
  });
});

describe('mimeTypeFor', () => {
  /** Known extensions get their type; unknown ones are binary. */
  it('maps extensions', () => {
    expect(mimeTypeFor('a.js')).toContain('text/javascript');
    expect(mimeTypeFor('a.CSS')).toContain('text/css');
    expect(mimeTypeFor('a.woff2')).toBe('font/woff2');
    expect(mimeTypeFor('a.xyz')).toBe('application/octet-stream');
  });
});

describe('createAppProtocolHandler', () => {
  /** The handler returns the file with its type, 404 for a missing one and 403 for an escape. */
  it('answers requests', async () => {
    const handle = createAppProtocolHandler(root);
    const ok = await handle(new Request('app://./js/plotly.min.js'));
    expect(ok.status).toBe(200);
    expect(ok.headers.get('content-type')).toContain('text/javascript');
    expect(await ok.text()).toBe('plotly');
    expect((await handle(new Request('app://./nope.js'))).status).toBe(404);
    expect((await handle(new Request('app://evil/index.html'))).status).toBe(403);
  });
});

describe('chooseUiSource', () => {
  /** Dev mode keeps the Vite server, with the environment override. */
  it('uses the dev server in development', () => {
    expect(chooseUiSource(true, {}, '/pkg/dist')).toEqual({ mode: 'dev', distRoot: null, url: 'http://127.0.0.1:5173' });
    expect(chooseUiSource(true, { ELECTRON_RENDERER_URL: 'http://127.0.0.1:9' }, '/pkg/dist').url).toBe('http://127.0.0.1:9');
  });

  /** The packaged app, or a source run given a built folder, loads app://. */
  it('uses app:// when packaged or given a built folder', () => {
    expect(chooseUiSource(false, {}, '/pkg/dist')).toEqual({ mode: 'app', distRoot: '/pkg/dist', url: APP_INDEX_URL });
    expect(chooseUiSource(true, { ASTROMETRICS_UI_DIST: '/x/dist', ELECTRON_RENDERER_URL: 'http://h' }, '/pkg/dist')).toEqual({
      mode: 'app',
      distRoot: '/x/dist',
      url: 'app://./index.html',
    });
  });
});
