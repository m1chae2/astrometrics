/**
 * @fileoverview Base platform contract for Electron OS integration.
 * Defines the lifecycle, notification, tray, logging, and process resolution hooks
 * that concrete platform adapters (Windows, Linux, macOS) implement.
 */

import path from 'path';

/**
 * Base platform adapter providing default cross-platform behavior.
 */
export class BasePlatform {
  /**
   * Applies platform-specific command-line switches early during boot.
   * Called before `app.whenReady()`.
   *
   * @param {Electron.App} _app - The Electron app instance.
   */
  initCommandLine(_app) {
    // Default: No custom command line switches
  }

  /**
   * Configures OS identity, application name, and taskbar group identifiers.
   *
   * @param {Electron.App} app - The Electron app instance.
   */
  configureIdentity(app) {
    app.name = 'astrometrics';
  }

  /**
   * Configures OS-level quick actions, Jump Lists, or dock menus.
   *
   * @param {Electron.App} _app - The Electron app instance.
   */
  setupUserTasks(_app) {
    // Default: No dynamic user tasks
  }

  /**
   * Configures platform-standard log directory paths and transports for electron-log.
   *
   * @param {Electron.App} app - The Electron app instance.
   * @param {Object} log - The electron-log instance.
   * @returns {string} The resolved log file path.
   */
  configureLogging(app, log) {
    log.transports.file.level = 'info';
    log.transports.file.maxSize = 5 * 1024 * 1024; // 5 MB maximum file size before rotation

    // Intercept and persist unhandled errors to disk without interrupting execution
    if (log.errorHandler && typeof log.errorHandler.startCatching === 'function') {
      log.errorHandler.startCatching({
        showDialog: false,
        onError({ error, processType }) {
          log.error(`[Unhandled ${processType} error]:`, error);
        }
      });
    }

    // Redact sensitive authentication tokens from disk logs
    if (log.hooks && Array.isArray(log.hooks)) {
      log.hooks.push((message) => {
        message.data = message.data.map((item) => {
          if (typeof item === 'string') {
            return item.replace(/ASTROMETRICS_SESSION_TOKEN=[^\s]+/g, 'ASTROMETRICS_SESSION_TOKEN=[REDACTED]');
          }
          return item;
        });
        return message;
      });
    }

    const logPath = path.join(app.getPath('userData'), 'logs', 'main.log');
    log.transports.file.resolvePathFn = () => logPath;
    return logPath;
  }

  /**
   * Adapts and validates native notification options for the host operating system.
   *
   * @param {Object} options - Raw notification options from renderer.
   * @param {Electron.App} _app - The Electron app instance.
   * @returns {Object} Platform-adapted options for new Notification(opts).
   */
  adaptNotificationOptions(options, _app) {
    return {
      title: options.title || 'Astrometrics',
      body: options.body || '',
      icon: options.icon,
      urgency: options.urgency || 'normal',
      tag: options.tag || undefined,
      silent: Boolean(options.silent),
      timeoutType: options.timeoutType || (options.urgency === 'critical' ? 'never' : 'default'),
      actions: options.actions
    };
  }

  /**
   * Resolves the command and arguments to start the backend.
   *
   * @param {string} appPath - Root application path.
   * @param {boolean} isPackaged - Whether the app is packaged.
   * @param {Object} [options] - Additional resolution options (e.g. fs reference).
   * @returns {{ command: string, args: string[], isBinary?: boolean }}
   */
  resolveBackendExecutable(appPath, isPackaged, options = {}) {
    throw new Error('resolveBackendExecutable must be implemented by platform adapter');
  }

  /**
   * Calculates popover (x, y) coordinates relative to the tray icon and screen boundaries.
   * Returns `null` if the host compositor or OS manages placement (e.g. Wayland).
   *
   * @param {Electron.Tray} _tray - The application tray instance.
   * @param {{ width: number, height: number }} _windowSize - Popover window dimensions.
   * @param {Object} [_screenModule] - Optional Electron screen module injection for testing.
   * @returns {{ x: number, y: number } | null}
   */
  getTrayPopoverPosition(_tray, _windowSize, _screenModule) {
    return null;
  }

  /**
   * Determines whether the tray popover should auto-hide when losing focus (blur).
   *
   * @returns {boolean}
   */
  shouldDismissTrayOnBlur() {
    return false;
  }

  /**
   * Determines whether closing all application windows should quit the process.
   *
   * @returns {boolean}
   */
  shouldQuitOnWindowAllClosed() {
    return true;
  }

  /**
   * Terminates a spawned backend process and all of its spawned child processes.
   *
   * @param {import('child_process').ChildProcess} childProcess
   */
  terminateProcessTree(childProcess) {
    if (!childProcess || childProcess.killed) return;
    try {
      childProcess.kill('SIGTERM');
    } catch {
      // Process may already be dead
    }
  }

  /**
   * Pauses heavy analysis and stacking subprocesses (e.g. Siril, solve-field)
   * without closing the application, freeing CPU/RAM for high-priority tasks.
   *
   * @param {Object} [options]
   * @param {Function} [options.execFn] Injected exec function for testing.
   * @returns {boolean} Whether pause command was issued.
   */
  pauseBackgroundPipelines({ execFn } = {}) {
    // Default: POSIX SIGSTOP pause
    const runExec = execFn || (globalThis.childProcessExec || null);
    if (!runExec) return false;
    try {
      runExec('pkill -STOP -f siril-cli || true');
      runExec('pkill -STOP -f solve-field || true');
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Resumes paused analysis and stacking subprocesses.
   *
   * @param {Object} [options]
   * @param {Function} [options.execFn] Injected exec function for testing.
   * @returns {boolean} Whether resume command was issued.
   */
  resumeBackgroundPipelines({ execFn } = {}) {
    // Default: POSIX SIGCONT resume
    const runExec = execFn || (globalThis.childProcessExec || null);
    if (!runExec) return false;
    try {
      runExec('pkill -CONT -f siril-cli || true');
      runExec('pkill -CONT -f solve-field || true');
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Returns platform-specific BrowserWindow options (e.g. Window Controls Overlay).
   *
   * @returns {Electron.BrowserWindowConstructorOptions}
   */
  getWindowOptions() {
    return {
      frame: true
    };
  }

  /**
   * Sets up platform-specific application menu and keyboard accelerators.
   *
   * @param {Electron.App} app - The Electron app instance.
   * @param {Object} [options] - Menu options and callbacks.
   * @param {Function} [options.onOpenFile] - Callback when Open File accelerator is pressed.
   * @param {boolean} [options.isDev] - Whether running in development mode.
   * @param {Object} [options.menuModule] - Injected Menu module for testing.
   * @returns {Electron.Menu|null} The built application menu.
   */
  setupApplicationMenu(app, { onOpenFile, isDev = false, menuModule } = {}) {
    const MenuClass = menuModule || (globalThis.ElectronMenu || null);
    if (!MenuClass) return null;

    const template = [
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
          {
            label: 'Close Window',
            accelerator: 'CmdOrCtrl+W',
            role: 'close'
          },
          {
            label: 'Quit Astrometrics',
            accelerator: 'CmdOrCtrl+Q',
            click: () => app.quit()
          }
        ]
      },
      {
        label: 'View',
        submenu: [
          { role: 'reload', accelerator: 'CmdOrCtrl+R' },
          { role: 'forceReload', accelerator: 'CmdOrCtrl+Shift+R' },
          ...(isDev ? [{ role: 'toggleDevTools', accelerator: 'CmdOrCtrl+Shift+I' }] : []),
          { type: 'separator' },
          { role: 'resetZoom', accelerator: 'CmdOrCtrl+0' },
          { role: 'zoomIn', accelerator: 'CmdOrCtrl+Plus' },
          { role: 'zoomOut', accelerator: 'CmdOrCtrl+-' },
          { type: 'separator' },
          { role: 'togglefullscreen', accelerator: 'F11' }
        ]
      },
      {
        label: 'Window',
        submenu: [
          { role: 'minimize', accelerator: 'CmdOrCtrl+M' },
          { role: 'zoom' }
        ]
      }
    ];

    const menu = MenuClass.buildFromTemplate(template);
    MenuClass.setApplicationMenu(menu);
    return menu;
  }
}
