/**
 * @fileoverview Windows 11 platform adapter for Astrometrics.
 * Handles AppUserModelId grouping, Windows Jump Lists, taskbar tray anchoring,
 * blur-based dismissals, and Windows backend resolution.
 */

import path from 'path';
import fs from 'fs';
import process from 'process';
import { execSync } from 'child_process';
import { screen } from 'electron';
import { BasePlatform } from './base.js';

/**
 * Windows-specific platform implementation.
 */
export class WindowsPlatform extends BasePlatform {
  /**
   * Sets AppUserModelId for taskbar grouping and notification routing.
   *
   * @param {Electron.App} app
   */
  configureIdentity(app) {
    app.name = 'astrometrics';
    app.setAppUserModelId('astrometrics');
  }

  /**
   * Configures the Windows Jump List tasks on the taskbar icon.
   *
   * @param {Electron.App} app
   */
  setupUserTasks(app) {
    try {
      app.setUserTasks([
        {
          program: process.execPath,
          arguments: '--mode="Planetarium"',
          title: 'Open Planetarium',
          description: 'Navigate the celestial sphere',
          iconPath: process.execPath,
          iconIndex: 0
        },
        {
          program: process.execPath,
          arguments: '--mode="Observatory Manager"',
          title: 'Observatory Manager',
          description: 'Mount, guider, and equipment controls',
          iconPath: process.execPath,
          iconIndex: 0
        },
        {
          program: process.execPath,
          arguments: '--mode="Image Processing"',
          title: 'Image Processing',
          description: 'FITS stacking, astrometry, and spectroscopy',
          iconPath: process.execPath,
          iconIndex: 0
        }
      ]);
    } catch {
      // User tasks may fail if taskbar is unavailable during headless tests
    }
  }

  /**
   * Formats notification options for Windows Toast Notifications.
   *
   * @param {Object} options
   * @param {Electron.App} app
   * @returns {Object}
   */
  adaptNotificationOptions(options, app) {
    const defaultIcon = path.join(app.getAppPath(), 'assets', 'orbit-smooth-128.png');
    const adapted = {
      title: options.title || 'Astrometrics',
      body: options.body || '',
      icon: options.icon || defaultIcon,
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
   * Resolves compiled backend binary or venv Python path for Windows.
   *
   * @param {string} appPath
   * @param {boolean} isPackaged
   * @returns {{ command: string, args: string[], isBinary: boolean }}
   */
  resolveBackendExecutable(appPath, isPackaged) {
    if (isPackaged) {
      const possibleBinaryPaths = [
        path.join(appPath, 'dist', 'backend', 'backend.exe'),
        path.join(appPath, 'backend', 'dist', 'backend', 'backend.exe'),
        process.resourcesPath ? path.join(process.resourcesPath, 'app', 'dist', 'backend', 'backend.exe') : null,
        process.resourcesPath ? path.join(process.resourcesPath, 'dist', 'backend', 'backend.exe') : null
      ].filter(Boolean);

      const foundPath = possibleBinaryPaths.find(p => fs.existsSync(p));
      if (foundPath) {
        return { command: foundPath, args: [], isBinary: true };
      }
    }

    // Dev or unpackaged fallback
    const venvPython = path.join(appPath, '.venv', 'Scripts', 'python.exe');
    if (fs.existsSync(venvPython)) {
      return { command: venvPython, args: ['-m', 'backend.main_backend'], isBinary: false };
    }

    return { command: 'python.exe', args: ['-m', 'backend.main_backend'], isBinary: false };
  }

  /**
   * Calculates anchor coordinates docking above or below the Windows taskbar.
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
    let y = 0;

    // Check if taskbar is at bottom or top
    if (trayBounds.y >= workArea.y + workArea.height / 2) {
      // Taskbar at bottom: place window directly above tray icon
      y = Math.round(trayBounds.y - height);
    } else {
      // Taskbar at top: place window directly below tray icon
      y = Math.round(trayBounds.y + trayBounds.height);
    }

    // Clamp coordinates strictly within the current screen's work area
    x = Math.max(workArea.x, Math.min(x, workArea.x + workArea.width - width));
    y = Math.max(workArea.y, Math.min(y, workArea.y + workArea.height - height));

    return { x, y };
  }

  /**
   * Windows users expect popovers to dismiss when clicking outside (blur).
   *
   * @returns {boolean}
   */
  shouldDismissTrayOnBlur() {
    return true;
  }

  /**
   * Terminates the backend process and all descendant worker processes
   * on Windows using taskkill /T /F.
   *
   * @param {import('child_process').ChildProcess} childProcess
   */
  terminateProcessTree(childProcess) {
    if (!childProcess || childProcess.killed || !childProcess.pid) return;
    try {
      execSync(`taskkill /pid ${childProcess.pid} /T /F`, { stdio: 'ignore' });
    } catch {
      try {
        childProcess.kill('SIGTERM');
      } catch {
        // Process already terminated
      }
    }
  }

  /**
   * Enables Windows 11 Window Controls Overlay (WCO) and native Snap Layouts.
   *
   * @returns {Electron.BrowserWindowConstructorOptions}
   */
  getWindowOptions() {
    return {
      titleBarStyle: 'hidden',
      titleBarOverlay: {
        color: '#181818',
        symbolColor: '#e0e0e0',
        height: 48
      }
    };
  }
}
