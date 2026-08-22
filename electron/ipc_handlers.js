import { ipcMain, dialog, Notification, autoUpdater } from 'electron';

/**
 * Registers all IPC handlers for the main process.
 * @param {BrowserWindow} mainWindow - Reference to the main application window.
 * @param {Function} createSecondaryWindow - Function to create the secondary window.
 * @param {BackendManager} [backendManager] - Owner of the backend session token.
 */
export function registerIpcHandlers(mainWindow, createSecondaryWindow, backendManager) {
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

  // Notification Support
  ipcMain.on('show-notification', (event, { title, body, icon }) => {
    new Notification({ title, body, icon }).show();
  });

  // Taskbar Progress
  ipcMain.on('set-progress', (event, { progress, mode }) => {
    if (!mainWindow) return;
    mainWindow.setProgressBar(progress, { mode: mode || 'normal' });
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
