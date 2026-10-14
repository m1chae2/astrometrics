/**
 * @fileoverview macOS platform adapter for Astrometrics.
 * Handles macOS dock menus, top menubar integration, native notification center,
 * and standard ~/Library/Logs log directory paths.
 */

import path from 'path';
import fs from 'fs';
import process from 'process';
import { screen, Menu } from 'electron';
import { BasePlatform } from './base.js';

/**
 * macOS platform implementation.
 */
export class MacOSPlatform extends BasePlatform {
  /**
   * Sets app name.
   *
   * @param {Electron.App} app
   */
  configureIdentity(app) {
    app.name = 'Astrometrics';
  }

  /**
   * Configures macOS Dock menu.
   *
   * @param {Electron.App} app
   */
  setupUserTasks(app) {
    if (!app.dock) return;
    try {
      const dockMenu = Menu.buildFromTemplate([
        {
          label: 'Open Planetarium',
          click: () => {
            app.emit('open-url', {}, 'astrometrics://mode/Planetarium');
          }
        },
        {
          label: 'Observatory Manager',
          click: () => {
            app.emit('open-url', {}, 'astrometrics://mode/Observatory%20Manager');
          }
        },
        {
          label: 'Image Processing',
          click: () => {
            app.emit('open-url', {}, 'astrometrics://mode/Image%20Processing');
          }
        }
      ]);
      app.dock.setMenu(dockMenu);
    } catch {
      // Dock configuration failure
    }
  }

  /**
   * Resolves standard macOS ~/Library/Logs path.
   *
   * @param {Electron.App} app
   * @param {Object} log
   * @returns {string}
   */
  configureLogging(app, log) {
    super.configureLogging(app, log);
    const logPath = path.join(app.getPath('home'), 'Library', 'Logs', 'Astrometrics', 'main.log');
    log.transports.file.resolvePathFn = () => logPath;
    return logPath;
  }

  /**
   * Formats notification options for macOS Notification Center.
   *
   * @param {Object} options
   * @param {Electron.App} _app
   * @returns {Object}
   */
  adaptNotificationOptions(options, _app) {
    const adapted = {
      title: options.title || 'Astrometrics',
      body: options.body || '',
      urgency: options.urgency || 'normal',
      tag: options.tag || undefined,
      silent: Boolean(options.silent),
      timeoutType: options.timeoutType || (options.urgency === 'critical' ? 'never' : 'default')
    };
    if (Array.isArray(options.actions) && options.actions.length > 0) {
      adapted.actions = options.actions.map(act =>
        typeof act === 'string' ? { type: 'button', text: act } : act
      );
    }
    return adapted;
  }

  /**
   * Resolves backend binary or virtualenv for macOS.
   *
   * @param {string} appPath
   * @param {boolean} isPackaged
   * @returns {{ command: string, args: string[], isBinary: boolean }}
   */
  resolveBackendExecutable(appPath, isPackaged) {
    if (isPackaged) {
      const candidatePaths = [
        path.join(appPath, 'dist', 'backend', 'backend'),
        path.join(appPath, 'backend', 'dist', 'backend', 'backend'),
        process.resourcesPath ? path.join(process.resourcesPath, 'app', 'dist', 'backend', 'backend') : null,
        process.resourcesPath ? path.join(process.resourcesPath, 'dist', 'backend', 'backend') : null
      ].filter(Boolean);

      const foundPath = candidatePaths.find(p => fs.existsSync(p));
      if (foundPath) {
        return { command: foundPath, args: [], isBinary: true };
      }
    }

    const localPython = path.join(appPath, '.venv', 'bin', 'python3');
    if (fs.existsSync(localPython)) {
      return { command: localPython, args: ['-m', 'backend.main_backend'], isBinary: false };
    }

    return { command: 'python3', args: ['-m', 'backend.main_backend'], isBinary: false };
  }

  /**
   * Calculates anchor coordinates directly below the macOS top menubar item.
   *
   * @param {Electron.Tray} tray
   * @param {{ width: number, height: number }} windowSize
   * @param {Object} [screenModule]
   * @returns {{ x: number, y: number }}
   */
  getTrayPopoverPosition(tray, { width, height }, screenModule = screen) {
    const trayBounds = tray.getBounds();
    const display = screenModule.getDisplayNearestPoint({ x: trayBounds.x, y: trayBounds.y });
    const workArea = display.workArea;

    let x = Math.round(trayBounds.x + (trayBounds.width / 2) - (width / 2));
    let y = Math.round(trayBounds.y + trayBounds.height);

    x = Math.max(workArea.x, Math.min(x, workArea.x + workArea.width - width));
    y = Math.max(workArea.y, Math.min(y, workArea.y + workArea.height - height));

    return { x, y };
  }

  /**
   * Popovers on macOS dismiss on blur.
   *
   * @returns {boolean}
   */
  shouldDismissTrayOnBlur() {
    return true;
  }

  /**
   * Closing all windows on macOS does not quit the application.
   *
   * @returns {boolean}
   */
  shouldQuitOnWindowAllClosed() {
    return false;
  }

  /**
   * Configures macOS system menu bar with standard Application and Edit menus.
   *
   * @param {Electron.App} app
   * @param {Object} [options]
   * @returns {Electron.Menu|null}
   */
  setupApplicationMenu(app, { onOpenFile, isDev = false, menuModule = Menu } = {}) {
    if (!menuModule) return null;

    const template = [
      {
        label: app.name || 'Astrometrics',
        submenu: [
          { role: 'about' },
          { type: 'separator' },
          { role: 'services' },
          { type: 'separator' },
          { role: 'hide' },
          { role: 'hideOthers' },
          { role: 'unhide' },
          { type: 'separator' },
          { role: 'quit' }
        ]
      },
      {
        label: 'File',
        submenu: [
          {
            label: 'Open FITS File...',
            accelerator: 'CmdOrCtrl+O',
            click: () => {
              if (typeof onOpenFile === 'function') {
                onOpenFile();
              }
            }
          },
          { type: 'separator' },
          { role: 'close' }
        ]
      },
      {
        label: 'Edit',
        submenu: [
          { role: 'undo' },
          { role: 'redo' },
          { type: 'separator' },
          { role: 'cut' },
          { role: 'copy' },
          { role: 'paste' },
          { role: 'selectAll' }
        ]
      },
      {
        label: 'View',
        submenu: [
          { role: 'reload' },
          { role: 'forceReload' },
          ...(isDev ? [{ role: 'toggleDevTools' }] : []),
          { type: 'separator' },
          { role: 'resetZoom' },
          { role: 'zoomIn' },
          { role: 'zoomOut' },
          { type: 'separator' },
          { role: 'togglefullscreen' }
        ]
      },
      {
        label: 'Window',
        submenu: [
          { role: 'minimize' },
          { role: 'zoom' },
          { type: 'separator' },
          { role: 'front' }
        ]
      }
    ];

    const menu = menuModule.buildFromTemplate(template);
    menuModule.setApplicationMenu(menu);
    return menu;
  }
}
