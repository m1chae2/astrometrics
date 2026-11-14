/**
 * @fileoverview Windows 11 platform adapter for Astrometrics.
 * Handles AppUserModelId grouping, Windows Jump Lists, taskbar tray anchoring,
 * blur-based dismissals, and Windows backend resolution.
 */

import path from 'path';
import fs from 'fs';
import process from 'process';
import { execSync } from 'child_process';
import { BasePlatform } from './base.js';

/** The workspaces the taskbar's Jump List opens, as `--mode` names and their descriptions. */
const JUMP_LIST_MODES = [
  ['Planetarium', 'Open Planetarium', 'Navigate the celestial sphere'],
  ['Observatory Manager', 'Observatory Manager', 'Mount, guider, and equipment controls'],
  ['Image Processing', 'Image Processing', 'FITS stacking, astrometry, and spectroscopy']
];

/**
 * Gives the app's own folder, `%LOCALAPPDATA%\Astrometrics`, as the Python side does.
 *
 * Local, not Roaming (`%APPDATA%`): its files belong to this computer, and caches and logs
 * should not be copied to other computers with the user's profile.
 *
 * @param {Electron.App} app
 * @returns {string}
 */
export function windowsAppFolder(app) {
  const localAppData = process.env.LOCALAPPDATA || path.join(app.getPath('home'), 'AppData', 'Local');
  return path.join(localAppData, 'Astrometrics');
}

/**
 * Gives the state folder, which holds the logs: `$XDG_STATE_HOME/astrometrics` when that is
 * set (as on Linux), otherwise `%LOCALAPPDATA%\Astrometrics\State`, as the Python side does.
 *
 * @param {Electron.App} app
 * @returns {string}
 */
export function windowsStateFolder(app) {
  if (process.env.XDG_STATE_HOME) return path.join(process.env.XDG_STATE_HOME, 'astrometrics');
  return path.join(windowsAppFolder(app), 'State');
}

/**
 * Gives the command that starts the app the way its desktop shortcut does: Windows PowerShell
 * running `build\windows\run_astrometrics.ps1` from the app's folder, with no window.
 *
 * @param {Electron.App} app
 * @returns {{ program: string, args: string[] }}
 */
export function launcherCommand(app) {
  const launcher = path.join(app.getAppPath(), 'build', 'windows', 'run_astrometrics.ps1');
  const program = path.join(
    process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
  );
  return { program, args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', launcher] };
}

/**
 * Windows-specific platform implementation.
 */
export class WindowsPlatform extends BasePlatform {
  /**
   * Asks Windows for the high-performance graphics card (an NVIDIA or AMD card next to an
   * Intel one), so the Planetarium and the image viewer draw on it. Windows otherwise often
   * picks the power-saving card. On a computer with one card this changes nothing. Unlike
   * Linux's `auto` choice, it does not switch to the power-saving card on battery.
   *
   * @param {Electron.App} app
   */
  initCommandLine(app) {
    app.commandLine.appendSwitch('force_high_performance_gpu');
  }

  /**
   * Sets AppUserModelId for taskbar grouping and notification routing, and keeps Electron's
   * own files (its browser caches, window position) in `%LOCALAPPDATA%\Astrometrics\Electron`.
   * Electron's default is the Roaming profile, which Windows copies between computers.
   *
   * @param {Electron.App} app
   */
  configureIdentity(app) {
    app.name = 'astrometrics';
    app.setAppUserModelId('astrometrics');
    app.setPath('userData', path.join(windowsAppFolder(app), 'Electron'));
  }

  /**
   * Writes Electron's log next to the backend's, in the state folder's `logs`, as on Linux.
   *
   * @param {Electron.App} app
   * @param {Object} log
   * @returns {string}
   */
  configureLogging(app, log) {
    super.configureLogging(app, log);
    const logPath = path.join(windowsStateFolder(app), 'logs', 'main.log');
    log.transports.file.resolvePathFn = () => logPath;
    return logPath;
  }

  /**
   * Registers the launcher, `build\windows\run_astrometrics.ps1`, as the handler of
   * `astrometrics://` links, as the desktop shortcut and the Jump List use it. The app runs
   * from its source folder, where `electron.exe` alone would start without the Vite server.
   * Windows adds the link after the arguments, so it arrives as the launcher's `-Url`. When
   * the app is already open, the new Electron hands the link to the open window.
   *
   * @param {Electron.App} app
   */
  registerProtocolClient(app) {
    const { program, args } = launcherCommand(app);
    app.setAsDefaultProtocolClient('astrometrics', program, [...args, '-Url']);
  }

  /**
   * Configures the Windows Jump List tasks on the taskbar icon.
   *
   * Each task runs the same launcher as the desktop shortcut,
   * `build\windows\run_astrometrics.ps1`, with `-Mode`. The app runs from its source folder,
   * where `electron.exe` alone would open an empty Electron window. When the app is already
   * open, the launcher reuses its backend and Vite server, and the new Electron hands the mode
   * to the open window (the `second-instance` event).
   *
   * @param {Electron.App} app
   */
  setupUserTasks(app) {
    const { program, args } = launcherCommand(app);
    // Paths are quoted, since a folder name may hold a space.
    const quoted = args.map((arg) => (/[\s\\/]/.test(arg) ? `"${arg}"` : arg)).join(' ');
    const icon = path.join(app.getAppPath(), 'assets', 'orbit.ico');
    try {
      app.setUserTasks(JUMP_LIST_MODES.map(([mode, title, description]) => ({
        program,
        arguments: `${quoted} -Mode "${mode}"`,
        title,
        description,
        iconPath: icon,
        iconIndex: 0
      })));
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

    // Dev or unpackaged fallback. build/windows/setup_windows.ps1 makes
    // `.venv-win`, so a checkout shared with Linux (whose `.venv` is there too)
    // keeps both. A plain `.venv` is still used when it is the only one.
    const venvPython = [
      path.join(appPath, '.venv-win', 'Scripts', 'python.exe'),
      path.join(appPath, '.venv', 'Scripts', 'python.exe')
    ].find(candidate => fs.existsSync(candidate));
    if (venvPython) {
      return { command: venvPython, args: ['-m', 'backend.main_backend'], isBinary: false };
    }

    return { command: 'python.exe', args: ['-m', 'backend.main_backend'], isBinary: false };
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
        // Same colour as the status header (--grey-dark in ui/common/styles/theme.css),
        // so the caption buttons do not show as a darker block at the right end.
        color: '#242426',
        symbolColor: '#e0e0e0',
        height: 48
      }
    };
  }
}
