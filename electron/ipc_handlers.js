import { ipcMain, dialog, Notification, autoUpdater, app, powerSaveBlocker, BrowserWindow } from 'electron';
import path from 'path';
import { getPlatform } from './platforms/index.js';

let powerSaveBlockerId = null;

/**
 * Registers all IPC handlers for the main process.
 * @param {BrowserWindow} mainWindow - Reference to the main application window.
 * @param {Function} createSecondaryWindow - Function to create the secondary window.
 * @param {BackendManager} [backendManager] - Owner of the backend session token.
 * @param {Function} [onUpdateTray] - Callback to update the tray menu.
 * @param {Function} [getTrayPopoverWindow] - Getter returning the live tray popover BrowserWindow.
 * @param {Object} [windowCoordinator] - Window coordinator holding multi-window helpers and mode map.
 * @param {BasePlatform} [platform] - Host OS platform adapter.
 * @param {PythonTerminalManager} [pythonTerminalManager] - Supervised Python terminal manager.
 */
export function registerIpcHandlers(
  mainWindow,
  createSecondaryWindow,
  backendManager,
  onUpdateTray,
  getTrayPopoverWindow,
  windowCoordinator = {},
  platform = getPlatform(),
  pythonTerminalManager = null
) {
  // Session token for the backend's WebSocket endpoints. The renderer cannot
  // read the CORS-protected HTTP endpoint when it runs under file://, so the
  // token is handed over here instead.
  ipcMain.handle('backend-session-token', () => (backendManager ? backendManager.sessionToken : ''));

  // Signal from renderer to open the target manager UI
  ipcMain.on('load-target-manager', () => {
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('load-target-manager');
    }
  });

  // Backend health check
  ipcMain.handle('backend-ping', async (event, url) => {
    const target = typeof url === 'string' && url ? url : (process.env.BACKEND_URL || 'http://127.0.0.1:5000/');
    try {
      const response = await fetch(target, { method: 'GET' });
      return { ok: response.ok, status: response.status, statusText: response.statusText };
    } catch (error) {
      return { ok: false, status: 0, statusText: String(error) };
    }
  });

  // Open-file dialog
  ipcMain.handle('dialog-open-file', async (event, options) => {
    const dialogOptions = typeof options === 'object' && options ? options : { properties: ['openFile'] };
    try {
      const result = await dialog.showOpenDialog(mainWindow || undefined, dialogOptions);
      if (result.canceled) return null;
      return result.filePaths;
    } catch (error) {
      console.warn('dialog-open-file failed:', error);
      return null;
    }
  });

  // Save-file dialog
  ipcMain.handle('dialog-save-file', async (event, options) => {
    const dialogOptions = typeof options === 'object' && options ? options : {};
    try {
      const result = await dialog.showSaveDialog(mainWindow || undefined, dialogOptions);
      if (result.canceled) return null;
      return result.filePath;
    } catch (error) {
      console.warn('dialog-save-file failed:', error);
      return null;
    }
  });

  // Open Matplotlib figure window
  ipcMain.handle('open-figure-window', (_event, plotPath) => {
    if (typeof windowCoordinator.createFigureWindow === 'function') {
      const win = windowCoordinator.createFigureWindow(plotPath);
      return { windowId: win.id };
    }
    return null;
  });

  // Open new display window
  ipcMain.handle('open-display-window', (_event, options) => {
    if (typeof windowCoordinator.createDisplayWindow === 'function') {
      const win = windowCoordinator.createDisplayWindow(options);
      return { windowId: win.id };
    }
    return null;
  });

  // Track active mode per window
  ipcMain.on('window-mode-changed', (event, mode) => {
    if (typeof windowCoordinator.setWindowMode === 'function') {
      windowCoordinator.setWindowMode(event.sender.id, mode);
    }
  });

  // Toggle secondary window (supports closing when false, and optional mode)
  ipcMain.on('toggle-secondary-window', (_event, enable, mode) => {
    if (enable) {
      createSecondaryWindow(mode);
    } else if (typeof windowCoordinator.closeSecondaryWindow === 'function') {
      windowCoordinator.closeSecondaryWindow();
    }
  });

  // Universal route display action across open windows
  ipcMain.handle('route-display-action', (event, intent) => {
    const { targetDisplay, targetElement, action, payload, focus = true } = intent || {};
    if (!targetDisplay) {
      return { handledRemotely: false };
    }

    const callerWebContentsId = event.sender.id;
    const windowModes = windowCoordinator.windowModes;
    if (windowModes) {
      for (const [webContentsId, mode] of windowModes.entries()) {
        if (mode === targetDisplay && webContentsId !== callerWebContentsId) {
          const targetWin = BrowserWindow.getAllWindows().find(
            (w) => !w.isDestroyed() && w.webContents.id === webContentsId
          );
          if (targetWin) {
            if (focus) {
              if (targetWin.isMinimized()) targetWin.restore();
              targetWin.show();
              targetWin.focus();
            }
            targetWin.webContents.send('remote-action', {
              action,
              payload,
              intent: { targetDisplay, targetElement, action, payload, focus },
            });
            return { handledRemotely: true, targetWindowId: targetWin.id };
          }
        }
      }
    }

    return { handledRemotely: false };
  });

  // Dynamic Tray Status update from renderer
  ipcMain.on('update-tray-status', (_event, status) => {
    if (typeof onUpdateTray === 'function') {
      onUpdateTray(status);
    }
  });

  // Power Save Blocker (prevents sleep during active image capture/guiding)
  ipcMain.on('set-power-save-blocker', (_event, { enable }) => {
    try {
      if (enable) {
        if (powerSaveBlockerId === null || !powerSaveBlocker.isStarted(powerSaveBlockerId)) {
          powerSaveBlockerId = powerSaveBlocker.start('prevent-app-suspension');
        }
      } else if (powerSaveBlockerId !== null) {
        if (powerSaveBlocker.isStarted(powerSaveBlockerId)) {
          powerSaveBlocker.stop(powerSaveBlockerId);
        }
        powerSaveBlockerId = null;
      }
    } catch (err) {
      console.warn('Failed to set power save blocker:', err);
    }
  });

  // Capture screenshot of a specific window or main window (Base64 PNG)
  ipcMain.handle('capture-screenshot', async (_event, { mode = null } = {}) => {
    try {
      let targetWin = mainWindow;
      if (mode && windowCoordinator.windowModes) {
        for (const [winId, m] of windowCoordinator.windowModes.entries()) {
          if (m === mode) {
            const match = BrowserWindow.getAllWindows().find(w => !w.isDestroyed() && w.webContents.id === winId);
            if (match) {
              targetWin = match;
              break;
            }
          }
        }
      }
      if (!targetWin || targetWin.isDestroyed()) return null;
      const nativeImg = await targetWin.webContents.capturePage();
      return nativeImg.toDataURL(); // data:image/png;base64,...
    } catch (err) {
      console.warn('Failed to capture window screenshot:', err);
      return null;
    }
  });

  // Pause or resume background processing pipelines via platform adapter
  ipcMain.handle('pause-pipelines', () => {
    return platform.pauseBackgroundPipelines();
  });

  ipcMain.handle('resume-pipelines', () => {
    return platform.resumeBackgroundPipelines();
  });

  // Python Terminal & MATLAB-style Workspace IPC Handlers
  ipcMain.handle('python-terminal-execute', async (event, { code, timeoutMs = 60000 }) => {
    if (!pythonTerminalManager) {
      return { status: 'error', stdout: '', stderr: 'PythonTerminalManager is not initialized' };
    }
    const webContents = event.sender;
    const res = await pythonTerminalManager.executeScript({
      code,
      timeoutMs,
      onOutput: (chunk) => {
        if (!webContents.isDestroyed()) {
          webContents.send('python-terminal-output', chunk);
        }
      },
      onFigure: (plotPath) => {
        if (!webContents.isDestroyed()) {
          webContents.send('python-terminal-figure', plotPath);
        }
      }
    });

    // Notify all windows of updated workspace variables
    if (res.workspace && !webContents.isDestroyed()) {
      webContents.send('python-terminal-workspace-updated', res.workspace);
    }
    return res;
  });

  ipcMain.handle('python-terminal-get-workspace', async () => {
    if (!pythonTerminalManager) return [];
    return pythonTerminalManager.getWorkspaceVariables();
  });

  ipcMain.handle('python-terminal-completions', async (_event, { text = '' } = {}) => {
    if (!pythonTerminalManager) return [];
    return pythonTerminalManager.getCompletions(text);
  });

  // UI Navigation from MCP / Remote
  ipcMain.handle('ui-navigate-mode', async (_event, { mode, target = null } = {}) => {
    if (!mode) return false;
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send('navigate-mode', mode);
      if (target) {
        mainWindow.webContents.send('remote-action', { action: 'targetSelected', payload: target });
      }
      return true;
    }
    return false;
  });

  // UI Variable Inspector
  ipcMain.handle('ui-inspect-variable', async (_event, { variableName } = {}) => {
    if (!variableName) return false;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('inspect-variable', { variableName });
      return true;
    }
    return false;
  });

  // UI Confirmation Dialog
  ipcMain.handle('ui-request-confirmation', async (_event, { title, message, buttons = ['Confirm', 'Cancel'] } = {}) => {
    const targetWin = mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
    const result = await dialog.showMessageBox(targetWin || undefined, {
      type: 'question',
      buttons,
      defaultId: 0,
      cancelId: 1,
      title: title || 'Astrometrics Confirmation',
      message: message || 'Confirm this action?'
    });
    return {
      confirmed: result.response === 0,
      buttonIndex: result.response,
      buttonText: buttons[result.response] || ''
    };
  });

  // Notification Support with Tag-based Coalescing, Cooldown Throttling, and Auto-Dismissal
  const activeNotifications = new Map();
  const notificationCooldowns = new Map();
  const NOTIFICATION_COOLDOWN_MS = 3000;
  const NOTIFICATION_AUTO_CLOSE_MS = 5000;

  ipcMain.on('show-notification', (_event, rawOptions) => {
    try {
      if (!Notification.isSupported()) return;
      const notificationOptions = platform.adaptNotificationOptions(rawOptions || {}, app);
      const tag = notificationOptions.tag || null;
      const bodyKey = `${tag || notificationOptions.title || 'default'}:${notificationOptions.body || ''}`;
      const now = Date.now();

      // Cooldown throttling: Suppress identical notification payloads within 3 seconds
      const lastShown = notificationCooldowns.get(bodyKey) || 0;
      if (now - lastShown < NOTIFICATION_COOLDOWN_MS) {
        return;
      }
      notificationCooldowns.set(bodyKey, now);

      // Clean up old cooldown entries periodically
      if (notificationCooldowns.size > 100) {
        for (const [key, timestamp] of notificationCooldowns.entries()) {
          if (now - timestamp > NOTIFICATION_COOLDOWN_MS * 2) {
            notificationCooldowns.delete(key);
          }
        }
      }

      // Close previous notification if one is active under the same tag
      if (tag && activeNotifications.has(tag)) {
        try {
          const prev = activeNotifications.get(tag);
          if (prev.notification && typeof prev.notification.close === 'function') {
            prev.notification.close();
          }
          if (prev.timer) {
            clearTimeout(prev.timer);
          }
        } catch {
          // Ignore close failures on already-dismissed notifications
        }
        activeNotifications.delete(tag);
      }

      const notification = new Notification(notificationOptions);

      notification.on('click', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.show();
          mainWindow.focus();
        }
      });

      notification.on('action', (_evt, index) => {
        if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents) {
          mainWindow.webContents.send('notification-action-clicked', { index });
        }
      });

      notification.show();

      // Auto-dismiss non-critical notifications after 5 seconds to prevent permanent accumulation in Ubuntu/GNOME tray
      let autoCloseTimer = null;
      if (notificationOptions.urgency !== 'critical') {
        autoCloseTimer = setTimeout(() => {
          try {
            notification.close();
          } catch {
            // Process or notification may already be closed
          }
          if (tag && activeNotifications.get(tag)?.notification === notification) {
            activeNotifications.delete(tag);
          }
        }, NOTIFICATION_AUTO_CLOSE_MS);
      }

      if (tag) {
        activeNotifications.set(tag, { notification, timer: autoCloseTimer });
      }
    } catch (err) {
      console.warn('Failed to display native notification:', err);
    }
  });

  // Taskbar Progress
  ipcMain.on('set-progress', (event, { progress, mode }) => {
    if (!mainWindow) return;
    mainWindow.setProgressBar(progress, { mode: mode || 'normal' });
  });

  // Tray Popover Actions — routed from the TrayPopover React component
  // Emergency Park intentionally keeps the popover open so the user can
  // confirm mount state after triggering the park command.
  ipcMain.on('tray-popover-action', async (_event, { action, payload }) => {
    switch (action) {
      case 'park':
        // Fire park request; the popover stays open for the user to confirm
        try {
          await fetch('http://127.0.0.1:5000/api/telescope/park', { method: 'POST' });
        } catch (err) {
          console.warn('[TrayPopover] Emergency park request failed:', err);
        }
        break;

      case 'navigate': {
        const mode = payload?.mode;
        if (!mode) break;
        if (mainWindow && !mainWindow.isDestroyed()) {
          if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.show();
          mainWindow.focus();
          mainWindow.webContents.send('navigate-mode', mode);
        }
        // Dismiss the popover after switching workspace
        const popoverNav = typeof getTrayPopoverWindow === 'function' ? getTrayPopoverWindow() : null;
        if (popoverNav && !popoverNav.isDestroyed()) popoverNav.hide();
        break;
      }

      case 'open-app': {
        if (mainWindow && !mainWindow.isDestroyed()) {
          if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.show();
          mainWindow.focus();
        }
        // Dismiss the popover after revealing the main window
        const popoverOpen = typeof getTrayPopoverWindow === 'function' ? getTrayPopoverWindow() : null;
        if (popoverOpen && !popoverOpen.isDestroyed()) popoverOpen.hide();
        break;
      }

      case 'quit':
        app.quit();
        break;

      case 'hide-popover': {
        const popoverHide = typeof getTrayPopoverWindow === 'function' ? getTrayPopoverWindow() : null;
        if (popoverHide && !popoverHide.isDestroyed()) popoverHide.hide();
        break;
      }

      default:
        console.warn('[TrayPopover] Unknown action received:', action);
    }
  });

  // Auto-Updater Events
  autoUpdater.on('update-available', () => {
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Available',
      message: 'A new version is available. Downloading now...',
      buttons: ['OK']
    });
  });

  autoUpdater.on('update-downloaded', () => {
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Ready',
      message: 'Update downloaded. Restart now to install?',
      buttons: ['Restart', 'Later']
    }).then((result) => {
      if (result.response === 0) {
        autoUpdater.quitAndInstall();
      }
    });
  });
}
