/**
 * @fileoverview The native menu of the system tray icon.
 *
 * The menu shows the observatory status, a workspace switcher and three
 * actions (Park, Open App, Quit). The desktop draws the menu itself, right
 * under the tray icon. On GNOME (Wayland and XWayland) an app cannot read the
 * icon position or place its own windows, so a native menu is the only way to
 * open something that sits under the icon.
 *
 * The status lines come from the backend through `backend_client.js` and are
 * refreshed on a timer. The timer only asks the backend while it is reachable.
 * While the menu is open the new lines wait, because rebuilding an open menu
 * can make it flicker or close on some desktops.
 */

import { callRpc } from './backend_client.js';
import { emergencyPark } from './emergency_park.js';

/** How often (ms) the status lines are refreshed. */
export const TRAY_REFRESH_MS = 5000;

/** Longest time (ms) one status request may take before it counts as offline. */
const STATUS_TIMEOUT_MS = 3000;

/** The workspaces offered in the menu: the label shown and the mode name sent to the window. */
export const TRAY_WORKSPACES = [
  { label: 'Image Viewer', mode: 'Image Viewer' },
  { label: 'Astronomy Manager', mode: 'Astronomy Manager' },
  { label: 'Planetarium', mode: 'Planetarium' },
  { label: 'Image Processing', mode: 'Image Processing' },
  { label: 'Observatory', mode: 'Observatory Manager' },
  { label: 'Observation Mgr', mode: 'Observation Manager' },
];

/** The status shown when the backend cannot be reached. */
export const OFFLINE_STATUS = Object.freeze({
  online: false,
  mount: 'Disconnected',
  target: null,
  ra: null,
  dec: null,
  altitude: null,
  azimuth: null,
  observatoryName: null,
  simulator: false,
  simulatorLabel: null,
});

/**
 * Takes the useful part out of a backend reply. The backend may wrap the value
 * in a `data` field.
 *
 * @param {object|null} envelope The parsed JSON-RPC reply.
 * @returns {*} The result value, or null when the reply holds an error or nothing.
 */
function unwrapResult(envelope) {
  if (!envelope || envelope.error) return null;
  const result = envelope.result;
  if (result && typeof result === 'object' && 'data' in result) return result.data;
  return result ?? null;
}

/**
 * Runs one backend method and returns its value, or null when it fails.
 *
 * @param {string} method The backend method name.
 * @param {object} options Options passed on to `callRpc`.
 * @returns {Promise<*>} The value, or null.
 */
async function askBackend(method, options) {
  try {
    const reply = await callRpc(method, {}, {
      id: `tray-${method}`,
      signal: AbortSignal.timeout(STATUS_TIMEOUT_MS),
      ...options,
    });
    return reply.ok ? unwrapResult(reply.envelope) : null;
  } catch {
    return null;
  }
}

/**
 * Reads the telescope status and the observatory details from the backend.
 *
 * Never throws. When the telescope status cannot be read, the result is the
 * offline status.
 *
 * @param {object} [options] Options passed on to `callRpc` (for tests).
 * @returns {Promise<object>} The status used to build the menu.
 */
export async function fetchTrayStatus(options = {}) {
  const telescope = await askBackend('telescope:status', options);
  if (!telescope || typeof telescope !== 'object') return { ...OFFLINE_STATUS };

  const connected = telescope.connectionStatus === 'Connected';
  const [source, equipment] = await Promise.all([
    askBackend('observatory:get_source', options),
    askBackend('observatory:get_active_telescope', options),
  ]);
  // The launch report says whether the app is off live hardware. A failed start is not shown as a simulator.
  const launch = source && source.launch;
  const simulator = Boolean(launch && launch.live_hardware === false) && !source.failed
    && !/fail/i.test(String(launch.label || ''));
  return {
    online: true,
    mount: connected ? (telescope.trackingStatus || 'Ready') : 'Disconnected',
    target: null,
    ra: connected ? (telescope.ra ?? null) : null,
    dec: connected ? (telescope.dec ?? null) : null,
    altitude: connected ? (telescope.altitude ?? null) : null,
    azimuth: connected ? (telescope.azimuth ?? null) : null,
    observatoryName: equipment && typeof equipment.name === 'string' ? equipment.name : null,
    simulator,
    simulatorLabel: simulator ? String(source.launch.label || 'SIMULATOR') : null,
  };
}

/**
 * Builds the menu template (the list of menu items) for a status.
 *
 * @param {object} status The status from `fetchTrayStatus`, plus an optional `target`.
 * @param {object} actions What the clickable items do.
 * @param {(mode: string) => void} actions.navigate Shows a workspace in the main window.
 * @param {() => void} actions.park Parks the mount.
 * @param {() => void} actions.openApp Shows the main window.
 * @param {() => void} actions.quit Quits the app.
 * @returns {object[]} Items for `Menu.buildFromTemplate`.
 */
export function buildTrayMenuTemplate(status, actions) {
  const name = status.observatoryName || 'Astrometrics';
  const header = status.simulator ? `Observatory — ${name} (${status.simulatorLabel || 'SIMULATOR'})` : `Observatory — ${name}`;
  const hasPosition = status.ra != null && status.dec != null;
  const hasHorizon = status.altitude != null && status.azimuth != null;
  return [
    { label: header, enabled: false },
    { label: `Mount: ${status.mount}`, enabled: false },
    { label: `Target: ${status.target || 'none'}`, enabled: false },
    { label: `RA/Dec: ${hasPosition ? `${status.ra} / ${status.dec}` : '—'}`, enabled: false },
    { label: `Alt/Az: ${hasHorizon ? `${status.altitude}° / ${status.azimuth}°` : '—'}`, enabled: false },
    { type: 'separator' },
    ...TRAY_WORKSPACES.map(({ label, mode }) => ({ label, click: () => actions.navigate(mode) })),
    { type: 'separator' },
    { label: 'Park', click: () => actions.park() },
    { label: 'Open App', click: () => actions.openApp() },
    { label: 'Quit', click: () => actions.quit() },
  ];
}

/**
 * Puts the native menu on the tray icon and keeps its status lines fresh.
 *
 * @param {object} deps What the menu needs from the app.
 * @param {Electron.Tray} deps.tray The tray icon.
 * @param {typeof Electron.Menu} deps.Menu Electron's `Menu`.
 * @param {() => (Electron.BrowserWindow|null)} deps.getMainWindow Returns the main window, if any.
 * @param {() => void} deps.quit Quits the app.
 * @param {(title: string, body: string) => void} deps.notify Shows a desktop notice.
 * @param {string} deps.platform Value of `process.platform`.
 * @param {object} [deps.log] A logger with `warn`.
 * @param {() => Promise<object>} [deps.fetchStatus] Reads the status (for tests).
 * @param {() => Promise<{ok: boolean, error?: string}>} [deps.park] Parks the mount (for tests).
 * @returns {{refresh: () => Promise<void>, setTarget: (name: (string|null)) => void, stop: () => void}}
 *   `refresh` reads the status and rebuilds the menu, `setTarget` sets the
 *   target line, and `stop` ends the timer.
 */
export function createTrayMenu({
  tray, Menu, getMainWindow, quit, notify, platform, log = console,
  fetchStatus = fetchTrayStatus, park = emergencyPark,
}) {
  let status = { ...OFFLINE_STATUS };
  let target = null;
  let menuOpen = false;
  let rebuildPending = false;
  let stopped = false;

  /** Shows the main window and brings it to the front. */
  const showMainWindow = () => {
    const win = getMainWindow();
    if (!win || win.isDestroyed()) return null;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    return win;
  };

  const actions = {
    navigate: (mode) => {
      const win = showMainWindow();
      if (win) win.webContents.send('navigate-mode', mode);
    },
    openApp: () => { showMainWindow(); },
    quit: () => quit(),
    park: async () => {
      const result = await park();
      if (!result.ok) {
        log.warn('[Tray] Emergency park failed:', result.error);
        notify('Emergency park failed', `The mount was not parked: ${result.error}. Park it manually.`);
      }
    },
  };

  /** Rebuilds the menu from the current status. Never throws. */
  const rebuild = () => {
    if (stopped || (tray.isDestroyed && tray.isDestroyed())) return;
    if (menuOpen) {
      rebuildPending = true;
      return;
    }
    rebuildPending = false;
    try {
      const shown = { ...status, target };
      const menu = Menu.buildFromTemplate(buildTrayMenuTemplate(shown, actions));
      // Menu open/close events exist on some Electron versions and platforms.
      // Where they never fire, the menu is simply rebuilt on the timer.
      if (typeof menu.on === 'function') {
        menu.on('menu-will-show', () => { menuOpen = true; });
        menu.on('menu-will-close', () => {
          menuOpen = false;
          if (rebuildPending) setTimeout(rebuild, 0);
        });
      }
      tray.setContextMenu(menu);
      tray.setToolTip(`Astrometrics • Mount: ${shown.mount} • Target: ${shown.target || 'none'}`);
    } catch (error) {
      log.warn('[Tray] Could not build the tray menu:', error);
    }
  };

  /** Reads the status from the backend and rebuilds the menu. Never throws. */
  const refresh = async () => {
    try {
      status = await fetchStatus();
    } catch {
      status = { ...OFFLINE_STATUS };
    }
    rebuild();
  };

  // Linux shows the menu by itself; macOS and Windows need the call.
  if (platform !== 'linux' && typeof tray.on === 'function') {
    tray.on('click', () => {
      try { tray.popUpContextMenu(); } catch (error) { log.warn('[Tray] Could not open the menu:', error); }
    });
  }

  rebuild();
  void refresh();
  // An offline backend costs one quick failed request per tick: the extra
  // detail requests are skipped until the telescope status answers again.
  const timer = setInterval(() => { void refresh(); }, TRAY_REFRESH_MS);
  if (typeof timer.unref === 'function') timer.unref();

  return {
    refresh,
    setTarget: (name) => { target = name || null; rebuild(); },
    stop: () => { stopped = true; clearInterval(timer); },
  };
}
