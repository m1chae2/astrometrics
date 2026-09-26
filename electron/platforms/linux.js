/**
 * @fileoverview Linux (Ubuntu 26.04 / GNOME / Wayland / X11) platform adapter.
 * Handles Wayland window decorations, XDG logging and desktop identity,
 * Freedesktop notification urgency mapping, and Linux backend binary/venv resolution.
 */

import path from 'path';
import fs from 'fs';
import process from 'process';
import { exec } from 'child_process';
import { BasePlatform } from './base.js';

/**
 * Linux platform implementation.
 */
export class LinuxPlatform extends BasePlatform {
  /**
   * Enables native Wayland Ozone rendering (with automatic X11 fallback) and
   * Wayland client/server window decoration interoperability on modern Linux.
   *
   * @param {Electron.App} app
   */
  initCommandLine(app) {
    // Auto-detect Wayland vs X11 session for crisp HiDPI rendering and native gestures
    app.commandLine.appendSwitch('ozone-platform-hint', 'auto');
    app.commandLine.appendSwitch('enable-features', 'WaylandWindowDecorations,WebRTCPipeWireCapturer');
  }

  /**
   * Sets app name and AppUserModelId for Wayland app_id and GNOME dock matching.
   *
   * @param {Electron.App} app
   */
  configureIdentity(app) {
    app.name = 'astrometrics';
    app.setAppUserModelId('astrometrics');
  }

  /**
   * Configures logging adhering to Linux XDG base directory specifications.
   *
   * @param {Electron.App} app
   * @param {Object} log
   * @returns {string}
   */
  configureLogging(app, log) {
    super.configureLogging(app, log);
    const stateHome = process.env.XDG_STATE_HOME || path.join(app.getPath('home'), '.local', 'state');
    const logPath = path.join(stateHome, 'astrometrics', 'logs', 'main.log');
    log.transports.file.resolvePathFn = () => logPath;
    return logPath;
  }

  /**
   * Formats notification options according to Freedesktop notification daemon standards.
   *
   * @param {Object} options
   * @param {Electron.App} app
   * @returns {Object}
   */
  adaptNotificationOptions(options, app) {
    const defaultIcon = path.join(app.getAppPath(), 'assets', 'orbit-smooth-128.png');
    const urgency = ['low', 'normal', 'critical'].includes(options.urgency)
      ? options.urgency
      : 'normal';

    const adapted = {
      title: options.title || 'Astrometrics',
      body: options.body || '',
      icon: options.icon || defaultIcon,
      urgency,
      tag: options.tag || undefined,
      silent: Boolean(options.silent),
      timeoutType: options.timeoutType || (urgency === 'critical' ? 'never' : 'default')
    };

    if (Array.isArray(options.actions) && options.actions.length > 0) {
      adapted.actions = options.actions.map(act =>
        typeof act === 'string' ? { type: 'button', text: act } : act
      );
    }
    return adapted;
  }

  /**
   * Resolves Linux backend executable candidate paths.
   *
   * @param {string} appPath
   * @param {boolean} isPackaged
   * @returns {{ command: string, args: string[], isBinary: boolean }}
   */
  resolveBackendExecutable(appPath, isPackaged) {
    if (isPackaged) {
      const possibleBinaryPaths = [
        path.join(appPath, 'dist', 'backend', 'backend'),
        path.join(appPath, 'backend', 'dist', 'backend', 'backend'),
        process.resourcesPath ? path.join(process.resourcesPath, 'app', 'dist', 'backend', 'backend') : null,
        process.resourcesPath ? path.join(process.resourcesPath, 'dist', 'backend', 'backend') : null
      ].filter(Boolean);

      const foundPath = possibleBinaryPaths.find(p => fs.existsSync(p));
      if (foundPath) {
        return { command: foundPath, args: [], isBinary: true };
      }
    }

    // Dev mode or venv fallback
    const localPython = path.join(appPath, '.venv', 'bin', 'python3');
    if (fs.existsSync(localPython)) {
      return { command: localPython, args: ['-m', 'backend.main_backend'], isBinary: false };
    }

    // Installed system package wrapper fallback
    return { command: 'astrometrics-backend', args: [], isBinary: false };
  }

  /**
   * On Wayland, compositor abstracts coordinate spaces; return null to let
   * window manager handle placement.
   *
   * @returns {null}
   */
  getTrayPopoverPosition() {
    return null;
  }

  /**
   * On Linux/Wayland, focus transition events are async and drop clicks;
   * blur auto-dismissal is disabled in favor of explicit dismissal.
   *
   * @returns {boolean}
   */
  shouldDismissTrayOnBlur() {
    return false;
  }

  /**
   * Kills the backend process and its child processes (e.g. Uvicorn, Python pools, INDI)
   * to ensure no orphaned processes leak CPU or drain laptop battery.
   *
   * @param {import('child_process').ChildProcess} childProcess
   */
  terminateProcessTree(childProcess) {
    if (!childProcess || childProcess.killed || !childProcess.pid) return;
    try {
      // Send SIGTERM to the process group
      process.kill(-childProcess.pid, 'SIGTERM');
    } catch {
      try {
        childProcess.kill('SIGTERM');
      } catch {
        // Process already terminated
      }
    }
  }

  /**
   * Pauses heavy analysis and stacking subprocesses using POSIX SIGSTOP.
   *
   * @param {Object} [options]
   * @param {Function} [options.execFn] Injected exec function for testing.
   * @returns {boolean}
   */
  pauseBackgroundPipelines({ execFn = exec } = {}) {
    try {
      execFn('pkill -STOP -f siril-cli || true', () => {});
      execFn('pkill -STOP -f solve-field || true', () => {});
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Resumes paused analysis and stacking subprocesses using POSIX SIGCONT.
   *
   * @param {Object} [options]
   * @param {Function} [options.execFn] Injected exec function for testing.
   * @returns {boolean}
   */
  resumeBackgroundPipelines({ execFn = exec } = {}) {
    try {
      execFn('pkill -CONT -f siril-cli || true', () => {});
      execFn('pkill -CONT -f solve-field || true', () => {});
      return true;
    } catch {
      return false;
    }
  }
}
