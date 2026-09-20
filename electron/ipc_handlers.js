import { ipcMain, dialog, Notification, autoUpdater, app, powerSaveBlocker } from 'electron';
import path from 'path';

let powerSaveBlockerId = null;

/**
 * Registers all IPC handlers for the main process.
 * @param {BrowserWindow} mainWindow - Reference to the main application window.
 * @param {Function} createSecondaryWindow - Function to create the secondary window.
 * @param {BackendManager} [backendManager] - Owner of the backend session token.
 * @param {Function} [onUpdateTray] - Callback to update the tray menu.
 * @param {Function} [getTrayPopoverWindow] - Getter returning the live tray popover BrowserWindow.
 */
export function registerIpcHandlers(mainWindow, createSecondaryWindow, backendManager, onUpdateTray, getTrayPopoverWindow) {
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

  // Toggle secondary window
  ipcMain.on('toggle-secondary-window', (event, enable) => {
    if (enable) {
      createSecondaryWindow();
    }
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

  // Notification Support
  ipcMain.on('show-notification', (_event, { title, body, icon, urgency, actions }) => {
    try {
      if (!Notification.isSupported()) return;
      const defaultIcon = path.join(app.getAppPath(), 'assets', 'orbit-smooth-128.png');
      const notificationOptions = {
        title: title || 'Astrometrics',
        body: body || '',
        icon: icon || defaultIcon,
        urgency: urgency || 'normal',
      };

      if (Array.isArray(actions) && actions.length > 0) {
        notificationOptions.actions = actions.map(act => (typeof act === 'string' ? { type: 'button', text: act } : act));
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
