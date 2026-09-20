/**
 * @fileoverview Electron main process for the Astrometrics application.
 * Orchestrates application lifecycle, window management, and backend services.
 */
import { app, BrowserWindow, Tray, Menu, nativeImage, session, nativeTheme } from 'electron';
import squirrelStartup from 'electron-squirrel-startup';
import path from 'path';
import process from 'process';
import os from 'os';
import log from 'electron-log';
import isDev from 'electron-is-dev';

// Modularized logic
import { registerIpcHandlers } from './ipc_handlers.js';
import { BackendManager } from './backend_manager.js';
import { createTrayPopoverWindow } from './tray_window.js';

// Handle squirrel startup for Windows
if (squirrelStartup) {
  app.quit();
}

// Ensure application identity matches .desktop entry for Wayland app_id and dock grouping
app.name = 'astrometrics';
if (process.platform === 'linux' || process.platform === 'win32') {
  app.setAppUserModelId('astrometrics');
}

// Ensure correct window decorations and sizing under Linux Wayland/X11
if (process.platform === 'linux') {
  app.commandLine.appendSwitch('enable-features', 'WaylandWindowDecorations');
}

/**
 * Extracts a candidate file path from application launch arguments.
 * Filters out Electron/Node binaries, switches, and current-directory tokens.
 *
 * @param {string[]} args Command-line arguments array.
 * @returns {string|null} Resolved file path if found, otherwise null.
 */
function extractFilePath(args) {
  if (!Array.isArray(args)) return null;
  for (const arg of args) {
    if (!arg || arg.startsWith('-') || arg === '.') continue;
    if (arg.endsWith('electron') || arg.endsWith('electron.js') || arg.endsWith('main.js')) continue;
    return path.resolve(arg);
  }
  return null;
}

/**
 * Extracts a requested workspace mode from --mode=<Name> command-line arguments.
 *
 * @param {string[]} args Command-line arguments array.
 * @returns {string|null} The target mode name or null.
 */
function extractModeArg(args) {
  if (!Array.isArray(args)) return null;
  for (const arg of args) {
    if (arg && arg.startsWith('--mode=')) {
      return arg.slice(7).replace(/^['"]|['"]$/g, '');
    }
  }
  return null;
}

let queuedFileToOpen = extractFilePath(process.argv);
let queuedModeToOpen = extractModeArg(process.argv);

// Configure Windows Jump List (app.setUserTasks)
if (process.platform === 'win32') {
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
  } catch (err) {
    log.warn('Failed to set Windows user tasks / Jump List:', err);
  }
}

// Single Instance Lock
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (_event, commandLine) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();

      const filePath = extractFilePath(commandLine);
      if (filePath && mainWindow.webContents) {
        mainWindow.webContents.send('open-file', filePath);
      }

      const modeArg = extractModeArg(commandLine);
      if (modeArg && mainWindow.webContents) {
        mainWindow.webContents.send('navigate-mode', modeArg);
      }
    }
  });
}

// OS File Association listener (macOS and portal events)
app.on('open-file', (event, filePath) => {
  event.preventDefault();
  if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents) {
    mainWindow.webContents.send('open-file', filePath);
  } else {
    queuedFileToOpen = filePath;
  }
});

// Configure Logging
log.transports.file.level = 'info';
log.transports.file.resolvePathFn = () => path.join(app.getPath('userData'), 'logs', 'main.log');

// Define global references
let mainWindow = null;
let secondaryWindow = null;
let splashWindow = null;
let trayPopoverWindow = null;
let splashShownAt = 0;
let isOpeningMainWindow = false;
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
 * Opens the main window once the backend has finished loading its star catalog.
 *
 * The backend's "ready" signal can arrive more than once (it is matched against
 * streamed output), so this guards against starting a second wait or window
 * while one is already underway.
 */
async function openMainWindowWhenBackendIsWarm() {
  if (mainWindow || isOpeningMainWindow) return;
  isOpeningMainWindow = true;
  await backendManager.waitUntilWarm();
  createMainWindow();
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
  frame: true,
  backgroundColor: '#181818',
  icon: getAppPath('assets', 'orbit-smooth-256.png'),
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

  // Deliver any pending file association or workspace mode once the frontend is ready
  mainWindow.webContents.once('did-finish-load', () => {
    if (queuedFileToOpen) {
      mainWindow.webContents.send('open-file', queuedFileToOpen);
      queuedFileToOpen = null;
    }
    if (queuedModeToOpen) {
      mainWindow.webContents.send('navigate-mode', queuedModeToOpen);
      queuedModeToOpen = null;
    }
  });

  registerIpcHandlers(mainWindow, createSecondaryWindow, backendManager, updateTrayMenu, () => trayPopoverWindow);
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

/**
 * Builds or refreshes the context menu for the system tray icon with live status.
 *
 * @param {Object} [status] Optional live observatory and workspace telemetry.
 * @param {string} [status.mountStatus] e.g. "Tracking", "Parked", "Slewing"
 * @param {string} [status.activeTarget] e.g. "M31 Andromeda Galaxy"
 * @param {string} [status.activeMode] Current active workspace
 */
function updateTrayMenu(status = {}) {
  if (!tray) return;

  const navigateTo = (mode) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
      mainWindow.webContents.send('navigate-mode', mode);
    }
  };

  const isTracking = status.mountStatus && /tracking/i.test(status.mountStatus);
  const isSlewing = status.mountStatus && /slew/i.test(status.mountStatus);
  const mountState = status.mountStatus || 'Ready';
  const mountDot = isTracking ? '● ' : isSlewing ? '● ' : '○ ';
  const targetName = status.activeTarget || 'None Selected';

  const tooltipLines = ['Astrometrics', `Observatory: ${mountState}`, `Target: ${targetName}`];
  tray.setToolTip(tooltipLines.join(' • '));

  const menuTemplate = [
    { label: 'Astrometrics', enabled: false },
    { type: 'separator' },
    { label: `${mountDot}Observatory: ${mountState}`, enabled: false },
    { label: `  Target: ${targetName}`, enabled: false },
    { type: 'separator' },
    {
      label: 'Switch Workspace',
      submenu: [
        { label: 'Image Viewer', click: () => navigateTo('Image Viewer') },
        { label: 'Astronomy Manager', click: () => navigateTo('Astronomy Manager') },
        { label: 'Planetarium', click: () => navigateTo('Planetarium') },
        { label: 'Image Processing', click: () => navigateTo('Image Processing') },
        { label: 'Observatory Manager', click: () => navigateTo('Observatory Manager') },
        { label: 'Observation Manager', click: () => navigateTo('Observation Manager') }
      ]
    },
    {
      label: 'Open Window',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.show();
          mainWindow.focus();
        }
      }
    },
    { type: 'separator' },
    {
      label: 'Emergency Park Telescope',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents) {
          mainWindow.webContents.send('emergency-park-mount');
        }
      }
    },
    { type: 'separator' },
    { label: 'Quit Astrometrics', click: () => app.quit() }
  ];

  try {
    // Store the built menu for use by the right-click handler bound in the tray
    // setup block. Do NOT call tray.setContextMenu() here — on Linux that call
    // intercepts all tray-icon clicks (including left-click) and prevents the
    // popover window's 'click' event from ever firing.
    tray._builtContextMenu = Menu.buildFromTemplate(menuTemplate);
  } catch (err) {
    log.warn('Failed to build tray context menu:', err);
  }
}

// App Initialization

app.on('ready', () => {
  // Ensure window manager uses dark frame background and widgets
  nativeTheme.themeSource = 'dark';

  // Listen to OS theme changes and notify open windows
  nativeTheme.on('updated', () => {
    const isDark = nativeTheme.shouldUseDarkColors;
    if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents) {
      mainWindow.webContents.send('system-theme-changed', isDark);
    }
    if (secondaryWindow && !secondaryWindow.isDestroyed() && secondaryWindow.webContents) {
      secondaryWindow.webContents.send('system-theme-changed', isDark);
    }
  });

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

  // Start backend and transition to UI once it has finished warming up. The
  // splash stays up until then (it is only dismissed by the main window's
  // ready-to-show), so nothing opens onto an empty Planetarium.
  backendManager.start(openMainWindowWhenBackendIsWarm);

  // System Tray
  try {
    const trayIconPath = getAppPath('assets', 'tray-icon-22.png');
    const trayIcon = nativeImage.createFromPath(trayIconPath);
    tray = new Tray(trayIcon);
    tray.setToolTip('Astrometrics');
    updateTrayMenu();

    // Tray Popover — frameless popover window toggled by left-clicking the tray icon
    trayPopoverWindow = createTrayPopoverWindow(
      tray,
      getAppPath,
      isDev,
      path.join(__dirname, 'preload.js'),
    );

    // Right-click shows the native fallback context menu.
    // This is kept separate from setContextMenu() which would block left-click
    // on Linux by intercepting all tray clicks before 'click' can fire.
    tray.on('right-click', () => {
      if (tray._builtContextMenu) {
        tray.popUpContextMenu(tray._builtContextMenu);
      }
    });
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
