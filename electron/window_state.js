/**
 * @fileoverview Persists the main window's size, position, and maximized
 * state across launches so the app reopens where the user left it, matching
 * the convention of native GNOME/GTK applications.
 */

import fs from 'fs';
import path from 'path';
import log from 'electron-log';

const STATE_FILE_NAME = 'window-state.json';

/** Minimum logical size accepted from a saved state file, to guard against a corrupt or stale save. */
const MIN_WIDTH = 640;
const MIN_HEIGHT = 480;

/**
 * Reads the saved window bounds for `app`, if any.
 *
 * @param {Electron.App} app
 * @returns {{ width: number, height: number, x?: number, y?: number, isMaximized: boolean } | null}
 */
export function loadWindowState(app) {
  const statePath = path.join(app.getPath('userData'), STATE_FILE_NAME);
  try {
    const raw = fs.readFileSync(statePath, 'utf8');
    const state = JSON.parse(raw);
    if (
      typeof state.width === 'number' && state.width >= MIN_WIDTH &&
      typeof state.height === 'number' && state.height >= MIN_HEIGHT
    ) {
      return state;
    }
  } catch {
    // No saved state yet, or it is unreadable/corrupt; caller falls back to defaults.
  }
  return null;
}

/**
 * Watches `win` for resize/move/close and saves its bounds and maximized
 * state to disk (debounced), so the next launch can restore them.
 *
 * @param {Electron.BrowserWindow} win
 * @param {Electron.App} app
 */
export function trackWindowState(win, app) {
  const statePath = path.join(app.getPath('userData'), STATE_FILE_NAME);
  let saveTimeout = null;

  const persist = () => {
    if (win.isDestroyed()) return;
    const isMaximized = win.isMaximized();
    // getNormalBounds() reports the pre-maximize size even while maximized,
    // so restoring later doesn't snap the window to full-screen dimensions.
    const bounds = win.getNormalBounds();
    const state = { ...bounds, isMaximized };
    try {
      fs.mkdirSync(path.dirname(statePath), { recursive: true });
      fs.writeFileSync(statePath, JSON.stringify(state));
    } catch (err) {
      log.debug('Failed to save window state:', err);
    }
  };

  const scheduleSave = () => {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(persist, 500);
  };

  win.on('resize', scheduleSave);
  win.on('move', scheduleSave);
  win.on('close', () => {
    clearTimeout(saveTimeout);
    persist();
  });
}
