/**
 * @fileoverview Manages the system tray popover BrowserWindow for Astrometrics.
 *
 * Creates a frameless, transparent, always-on-top popover window toggled by
 * clicking the tray icon.
 *
 * Wayland notes:
 *   - tray.getBounds() and getCursorScreenPoint() both return {x:0,y:0} on
 *     Wayland — absolute coordinates are not exposed to apps by the compositor.
 *   - BrowserWindow.setPosition() is a hint that Wayland compositors are free
 *     to ignore; the window typically appears wherever the compositor decides.
 *   - The blur event is unreliable on Wayland (focus transitions are async and
 *     compositor-controlled, so blur can arrive hundreds of ms late or not at
 *     all). Blur-based auto-dismiss is therefore removed. Dismissal is handled
 *     explicitly: tray re-click, action buttons inside the popover, or Escape.
 *   - alwaysOnTop is set to the 'pop-up-menu' level so the popover floats
 *     above the main maximised Astrometrics window.
 *
 * Double-fire guard:
 *   Some Linux AppIndicator setups emit 'click' twice per physical click.
 *   A 500 ms debounce lock on the toggle absorbs the second synthetic event.
 */

import { BrowserWindow } from 'electron';
import log from 'electron-log';
import { getPlatform } from './platforms/index.js';

/** Width of the popover window in logical pixels. */
export const POPOVER_WIDTH = 340;

/** Height of the popover window in logical pixels. */
export const POPOVER_HEIGHT = 460;

/**
 * Minimum interval (ms) between successive toggle calls. Guards against Linux
 * AppIndicator implementations that fire 'click' twice per physical click.
 */
const TOGGLE_DEBOUNCE_MS = 500;

/**
 * Creates the tray popover BrowserWindow and binds a debounced click-toggle
 * to the provided tray instance.
 *
 * Positioning and blur dismissal are delegated to the Platform Adapter:
 * - On Windows and macOS, the window docks precisely relative to the tray icon
 *   and automatically dismisses on blur.
 * - On Linux (Wayland), coordinates are compositor-managed and dismissal is explicit.
 *
 * @param {Electron.Tray} tray - The application system tray instance.
 * @param {(...parts: string[]) => string} getAppPath - Resolver for app-root paths.
 * @param {boolean} isDev - Whether the app is running in development mode.
 * @param {string} preloadPath - Absolute path to the shared preload script.
 * @param {BasePlatform} [platform] - Host OS platform adapter.
 * @returns {BrowserWindow} The created (hidden) popover window.
 */
export function createTrayPopoverWindow(tray, getAppPath, isDev, preloadPath, platform = getPlatform()) {
  const win = new BrowserWindow({
    width: POPOVER_WIDTH,
    height: POPOVER_HEIGHT,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,     // Allow the user to reposition manually on Wayland
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    icon: getAppPath('assets', 'orbit.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: preloadPath,
    },
  });

  // 'pop-up-menu' level ensures the popover floats above the main maximised
  // Astrometrics window, which is the default BrowserWindow 'normal' level.
  win.setAlwaysOnTop(true, 'pop-up-menu');

  // Platform-specific auto-dismiss on blur (Windows 11 and macOS desktop convention)
  if (platform.shouldDismissTrayOnBlur()) {
    win.on('blur', () => {
      if (!win.isDestroyed() && win.isVisible()) {
        log.info('[TrayPopover] blur event -> hiding');
        win.hide();
      }
    });
  }

  // Notify renderer of visibility transitions to pause/resume background timers
  win.on('show', () => {
    if (!win.isDestroyed() && win.webContents) {
      win.webContents.send('tray-visibility-changed', true);
    }
  });

  win.on('hide', () => {
    if (!win.isDestroyed() && win.webContents) {
      win.webContents.send('tray-visibility-changed', false);
    }
  });

  if (isDev) {
    const devUrl = process.env.ELECTRON_RENDERER_URL || 'http://127.0.0.1:5173';
    win.loadURL(`${devUrl}/tray_popover.html`).catch((err) => {
      log.warn('[TrayPopover] Dev URL failed, loading from dist:', err);
      win.loadFile(getAppPath('dist', 'tray_popover.html'));
    });
  } else {
    win.loadFile(getAppPath('dist', 'tray_popover.html'));
  }

  /** Timestamp of the last toggle call — used by the debounce. */
  let lastToggledAt = 0;

  /**
   * Toggles the popover visibility. Debounced to absorb double-fire events from
   * Linux AppIndicator implementations that emit 'click' twice per physical click.
   */
  const togglePopover = () => {
    if (win.isDestroyed()) return;

    const now = Date.now();
    if (now - lastToggledAt < TOGGLE_DEBOUNCE_MS) {
      log.info('[TrayPopover] toggle debounced');
      return;
    }
    lastToggledAt = now;

    if (win.isVisible()) {
      log.info('[TrayPopover] hiding');
      win.hide();
    } else {
      // Delegate positioning calculation to platform adapter
      const position = platform.getTrayPopoverPosition(tray, {
        width: POPOVER_WIDTH,
        height: POPOVER_HEIGHT
      });

      if (position && typeof position.x === 'number' && typeof position.y === 'number') {
        win.setPosition(position.x, position.y, false);
      }

      log.info('[TrayPopover] showing');
      win.show();
      win.focus();
    }
  };

  tray.on('click', togglePopover);

  return win;
}
