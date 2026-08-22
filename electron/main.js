/**
 * @fileoverview Electron main process for the Astrometrics application.
 * Orchestrates application lifecycle, window management, and backend services.
 */
import { app, BrowserWindow, Tray, Menu, nativeImage, session } from 'electron';
import squirrelStartup from 'electron-squirrel-startup';
import path from 'path';
import process from 'process';
import os from 'os';
import log from 'electron-log';
import isDev from 'electron-is-dev';

// Modularized logic
import { registerIpcHandlers } from './ipc_handlers.js';
import { BackendManager } from './backend_manager.js';

// Handle squirrel startup for Windows
if (squirrelStartup) {
  app.quit();
}

// Single Instance Lock
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

// Configure Logging
log.transports.file.level = 'info';
log.transports.file.resolvePathFn = () => path.join(app.getPath('userData'), 'logs', 'main.log');

// Define global references
let mainWindow = null;
let secondaryWindow = null;
let splashWindow = null;
let splashShownAt = 0;
let tray = null;
const backendManager = new BackendManager(app);

// Global State
const __dirname = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'));
const getAppPath = (...parts) => path.join(app.getAppPath(), ...parts);

// Minimum time the splash window stays up, just enough to avoid a flash of
// unstyled content while the main window's renderer does its initial paint.
// Views now mount on demand (see ui/App.tsx's visitedModes) rather than all
// preloading their data at boot, so this no longer needs to cover that work.
const SPLASH_MIN_DURATION_MS = 500;

/**
 * Creates and shows the splash window immediately on app launch.
 *
 * Context isolation is strictly enabled and nodeIntegration disabled
 * to ensure that even the local splash HTML cannot execute arbitrary node code.
 */
function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 320,
    height: 290,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    icon: getAppPath('assets', 'orbit.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  splashWindow.loadFile(getAppPath('electron', 'splash.html'));
  splashShownAt = Date.now();
}

/**
 * Closes the splash window and reveals the main window, waiting out any
 * remaining time on SPLASH_MIN_DURATION_MS since the splash was shown.
 */
function dismissSplashAndShowMainWindow() {
  const elapsed = Date.now() - splashShownAt;
  const remaining = Math.max(SPLASH_MIN_DURATION_MS - elapsed, 0);
  setTimeout(() => {
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
    splashWindow = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      // maximize() before the window is shown; calling it while show:false is
      // still pending, some Linux window managers map (show) the window early.
      mainWindow.maximize();
      mainWindow.show();
    }
  }, remaining);
}

/**
 * Shared window configuration for main and secondary windows.
 *
 * Enforces strict security boundaries by disabling `nodeIntegration` and
 * enabling `contextIsolation`. Exposes only the whitelisted IPC API defined
 * in `preload.js` to the React renderer context.
 */
const getWindowOptions = () => ({
  show: true,
  width: 1280,
  height: 800,
  minWidth: 1024,
  minHeight: 700,
  icon: getAppPath('assets', 'orbit.png'),
  resizable: true,
  webPreferences: {
    nodeIntegration: false,
    contextIsolation: true,
    preload: path.join(__dirname, 'preload.js')
  }
});

/**
 * Creates the main application window.
 *
 * Initializes the React frontend and binds the IPC event handlers that bridge
 * the renderer process to the Python backend process (`backendManager`).
 */
async function createMainWindow() {
  mainWindow = new BrowserWindow({ ...getWindowOptions(), show: false });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.once('ready-to-show', dismissSplashAndShowMainWindow);

  if (isDev) {
    const devUrl = process.env.ELECTRON_RENDERER_URL || 'http://127.0.0.1:5173';
    mainWindow.loadURL(devUrl);
  } else {
    mainWindow.loadFile(getAppPath('dist', 'index.html'));
  }

  registerIpcHandlers(mainWindow, createSecondaryWindow, backendManager);
}

/**
 * Creates the secondary window for dual-screen setups.
 *
 * Functions identically to the main window but doesn't handle the backend
 * lifecycle. Useful for separating the Planetarium from the Processing views.
 */
function createSecondaryWindow() {
  if (secondaryWindow && !secondaryWindow.isDestroyed()) {
    secondaryWindow.focus();
    return;
  }
  secondaryWindow = new BrowserWindow(getWindowOptions());
  secondaryWindow.setMenuBarVisibility(false);

  if (isDev) {
    secondaryWindow.loadURL(process.env.ELECTRON_RENDERER_URL || 'http://127.0.0.1:5173');
  } else {
    secondaryWindow.loadFile(getAppPath('dist', 'index.html'));
  }

  secondaryWindow.on('closed', () => {
    secondaryWindow = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('secondary-window-closed');
    }
  });
}

// App Initialization

app.on('ready', () => {
  createSplashWindow();

  // ---------------------------------------------------------------------------
  // Content Security Policy
  // Locks down what the renderer can load/connect to. No unsafe-eval permitted.
  // Dev allows the Vite dev server; production is restricted to localhost only.
  // ---------------------------------------------------------------------------
  const devCsp = [
    "default-src 'self'",
    "connect-src 'self' http://127.0.0.1:5000 ws://127.0.0.1:5000 http://127.0.0.1:5173 ws://127.0.0.1:5173",
    // @vitejs/plugin-react injects an inline preamble script for Fast Refresh (HMR).
    // unsafe-inline is required in dev; production is bundled with no inline scripts.
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
  ].join('; ');

  const prodCsp = [
    "default-src 'self'",
    "connect-src 'self' http://127.0.0.1:5000 ws://127.0.0.1:5000",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
  ].join('; ');

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [isDev ? devCsp : prodCsp],
      },
    });
  });

  // Start backend and transition to UI when ready
  backendManager.start(() => {
    if (!mainWindow) createMainWindow();
  });

  // System Tray
  try {
    const trayIcon = nativeImage.createFromPath(getAppPath('assets', 'orbit.png')).resize({ width: 16, height: 16 });
    tray = new Tray(trayIcon);
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Open Astrometrics', click: () => mainWindow && mainWindow.show() },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() }
    ]));
  } catch (err) {
    log.warn('Tray creation failed:', err);
  }
});

app.on('window-all-closed', () => {
  backendManager.stop();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => backendManager.stop());

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});
