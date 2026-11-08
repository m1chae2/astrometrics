/**
 * @fileoverview Tests for the native tray menu in `electron/tray_menu.js`.
 * Electron's Tray and Menu are replaced by small fakes, so no window opens.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  buildTrayMenuTemplate,
  createTrayMenu,
  fetchTrayStatus,
  OFFLINE_STATUS,
  TRAY_REFRESH_MS,
  TRAY_WORKSPACES,
} from '../../electron/tray_menu.js';

/** A status like the one the backend gives for a connected, tracking mount. */
const TRACKING = {
  online: true, mount: 'Tracking', target: null, ra: '05 35 17', dec: '-05 23 28',
  altitude: '45.0', azimuth: '120.0', observatoryName: 'Backyard', simulator: false,
};

/** Fake click actions for the template. */
function fakeActions() {
  return { navigate: vi.fn(), park: vi.fn(), openApp: vi.fn(), quit: vi.fn() };
}

/** Builds a fake Menu whose built menus remember their template. */
function fakeMenu() {
  return {
    buildFromTemplate: vi.fn((template) => {
      const handlers: Record<string, () => void> = {};
      return { template, on: (name: string, fn: () => void) => { handlers[name] = fn; }, handlers };
    }),
  };
}

/** Builds a fake Tray. */
function fakeTray() {
  return {
    setContextMenu: vi.fn(), setToolTip: vi.fn(), popUpContextMenu: vi.fn(), on: vi.fn(),
    isDestroyed: () => false,
  };
}

/** Finds an item by label in the last menu set on the tray. */
function lastItem(tray: ReturnType<typeof fakeTray>, label: string) {
  const menu = tray.setContextMenu.mock.calls[tray.setContextMenu.mock.calls.length - 1][0];
  return menu.template.find((item: { label?: string }) => item.label === label);
}

/** Builds a menu controller with fake parts. */
function setup(overrides: Record<string, unknown> = {}) {
  const tray = fakeTray();
  const Menu = fakeMenu();
  const win = {
    isDestroyed: () => false, isMinimized: () => false, restore: vi.fn(), show: vi.fn(),
    focus: vi.fn(), webContents: { send: vi.fn() },
  };
  const deps = {
    tray, Menu, getMainWindow: () => win, quit: vi.fn(), notify: vi.fn(), platform: 'linux',
    log: { warn: vi.fn() }, fetchStatus: vi.fn().mockResolvedValue(TRACKING),
    park: vi.fn().mockResolvedValue({ ok: true }), ...overrides,
  };
  const controller = createTrayMenu(deps as never);
  return { tray, Menu, win, deps, controller };
}

afterEach(() => { vi.useRealTimers(); });

describe('buildTrayMenuTemplate', () => {
  /** The first lines are disabled status lines, then workspaces, then the actions. */
  it('lists status lines, workspaces and actions in order', () => {
    const template: any[] = buildTrayMenuTemplate(TRACKING, fakeActions());
    const labels = template.map((item: { label?: string; type?: string }) => item.label ?? item.type);
    expect(labels[0]).toBe('Observatory — Backyard');
    expect(labels.slice(1, 5)).toEqual([
      'Mount: Tracking', 'Target: none', 'RA/Dec: 05 35 17 / -05 23 28', 'Alt/Az: 45.0° / 120.0°',
    ]);
    expect(template.slice(0, 5).every((item: { enabled?: boolean }) => item.enabled === false)).toBe(true);
    expect(labels.slice(5)).toEqual([
      'separator', ...TRAY_WORKSPACES.map((w) => w.label), 'separator', 'Park', 'Open App', 'Quit',
    ]);
  });

  /** A simulated observatory is named in the first line. */
  it('marks the simulator in the first line', () => {
    const template: any[] = buildTrayMenuTemplate({ ...TRACKING, simulator: true }, fakeActions());
    expect(template[0].label).toContain('SIMULATOR');
  });

  /** The simulator's Park item says it parks the simulator; the real one does not. */
  it('labels Park as the simulator when it is one', () => {
    const labels = (template: any[]) => template.map((item) => item.label);
    expect(labels(buildTrayMenuTemplate({ ...TRACKING, simulator: true }, fakeActions()))).toContain('Park (simulator)');
    expect(labels(buildTrayMenuTemplate({ ...TRACKING }, fakeActions()))).toContain('Park');
  });

  /** Offline shows Disconnected and dashes. */
  it('shows Disconnected and dashes when offline', () => {
    const template: any[] = buildTrayMenuTemplate(OFFLINE_STATUS, fakeActions());
    expect(template[1].label).toBe('Mount: Disconnected');
    expect(template[3].label).toBe('RA/Dec: —');
  });

  /** Workspace items pass their mode name on. */
  it('sends the mode name for a workspace item', () => {
    const actions = fakeActions();
    const template: any[] = buildTrayMenuTemplate(TRACKING, actions);
    template.find((item: { label?: string }) => item.label === 'Observatory').click();
    expect(actions.navigate).toHaveBeenCalledWith('Observatory Manager');
  });
});

describe('fetchTrayStatus', () => {
  /** Builds a fake fetch that answers each method from a table. */
  function fetchFor(table: Record<string, unknown>) {
    return vi.fn(async (_url: string, init: { body: string }) => {
      const { method } = JSON.parse(init.body);
      if (!(method in table)) throw new Error('offline');
      return { ok: true, status: 200, statusText: 'OK', json: async () => ({ result: table[method] }) };
    });
  }

  /** A connected mount gives its status, name and simulator flag. */
  it('reads status, observatory name and simulator label', async () => {
    const fetchImpl = fetchFor({
      'telescope:status': { connectionStatus: 'Connected', trackingStatus: 'Parked', ra: '1', dec: '2', altitude: '3', azimuth: '4' },
      'observatory:get_source': { label: 'SIMULATOR', launch: { label: 'SIMULATOR', live_hardware: false, offline: true } },
      'observatory:get_active_telescope': { name: 'Dome' },
    });
    const status: any = await fetchTrayStatus({ fetchImpl, baseUrl: 'http://x' });
    expect(status).toMatchObject({ online: true, mount: 'Parked', observatoryName: 'Dome', simulator: true });
  });

  /** The target comes from the telescope status. */
  it('reads the target from the telescope status', async () => {
    const fetchImpl = fetchFor({
      'telescope:status': { connectionStatus: 'Connected', trackingStatus: 'Tracking', targetName: 'M 31' },
      'observatory:get_source': { launch: null },
      'observatory:get_active_telescope': { name: 'Dome' },
    });
    const status: any = await fetchTrayStatus({ fetchImpl, baseUrl: 'http://x' });
    expect(status.target).toBe('M 31');
  });

  /** The flag comes from the launch report, not from the label text. */
  it('uses the launch report for the simulator flag', async () => {
    const base = { 'telescope:status': { connectionStatus: 'Connected' }, 'observatory:get_active_telescope': { name: 'Dome' } };
    const ask = async (source: unknown): Promise<any> => fetchTrayStatus({
      fetchImpl: fetchFor({ ...base, 'observatory:get_source': source as any }), baseUrl: 'http://x' });
    // A label alone, or a null launch, is not a simulator.
    expect((await ask({ label: 'SIMULATOR', launch: null })).simulator).toBe(false);
    // A launch on live hardware is not a simulator.
    expect((await ask({ label: '', launch: { label: 'LIVE', live_hardware: true } })).simulator).toBe(false);
    // A test world on a real backend source (the TPM case) is flagged, with its own label.
    const tpm: any = await ask({ source: 'real', label: '', launch: { label: 'TPM RUN', live_hardware: false } });
    expect(tpm).toMatchObject({ simulator: true, simulatorLabel: 'TPM RUN' });
    // A failed start is not shown as a simulator.
    expect((await ask({ launch: { label: 'SIMULATOR FAILED', live_hardware: false }, failed: true })).simulator).toBe(false);
  });

  /** A refused connection gives the offline status and does not throw. */
  it('returns the offline status when the backend is unreachable', async () => {
    const status: any = await fetchTrayStatus({ fetchImpl: fetchFor({}), baseUrl: 'http://x' });
    expect(status.mount).toBe('Disconnected');
    expect(status.online).toBe(false);
  });

  /** A disconnected mount reads as Disconnected with no position. */
  it('reports a disconnected mount', async () => {
    const fetchImpl = fetchFor({ 'telescope:status': { connectionStatus: 'Disconnected', trackingStatus: 'Idle', ra: '0', dec: '0' } });
    const status: any = await fetchTrayStatus({ fetchImpl, baseUrl: 'http://x' });
    expect(status.mount).toBe('Disconnected');
    expect(status.ra).toBeNull();
  });
});

describe('createTrayMenu', () => {
  /** The menu is set on the tray at once, offline, then filled in. */
  it('sets an offline menu first, then the fetched one', async () => {
    const { tray, controller } = setup();
    expect(tray.setContextMenu).toHaveBeenCalledTimes(1);
    expect(lastItem(tray, 'Mount: Disconnected')).toBeTruthy();
    await controller.refresh();
    expect(lastItem(tray, 'Mount: Tracking')).toBeTruthy();
    controller.stop();
  });

  /** The timer refreshes the labels. */
  it('updates the labels on the refresh timer', async () => {
    vi.useFakeTimers();
    const fetchStatus = vi.fn().mockResolvedValueOnce(TRACKING).mockResolvedValue({ ...TRACKING, mount: 'Slewing' });
    const { tray, controller } = setup({ fetchStatus });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(TRAY_REFRESH_MS);
    expect(lastItem(tray, 'Mount: Slewing')).toBeTruthy();
    controller.stop();
  });

  /** A failing fetch gives Disconnected and no throw. */
  it('shows Disconnected when the fetch throws', async () => {
    const { tray, controller } = setup({ fetchStatus: vi.fn().mockRejectedValue(new Error('down')) });
    await expect(controller.refresh()).resolves.toBeUndefined();
    expect(lastItem(tray, 'Mount: Disconnected')).toBeTruthy();
    controller.stop();
  });

  /** A menu building error is logged, not thrown. */
  it('does not throw when the menu cannot be built', async () => {
    const Menu = { buildFromTemplate: vi.fn(() => { throw new Error('bad'); }) };
    const { deps, controller } = setup({ Menu });
    await expect(controller.refresh()).resolves.toBeUndefined();
    expect(deps.log.warn).toHaveBeenCalled();
    controller.stop();
  });

  /** An open menu is not replaced until it closes. */
  it('waits to rebuild while the menu is open', async () => {
    vi.useFakeTimers();
    const { tray, controller } = setup();
    await controller.refresh();
    const menu = tray.setContextMenu.mock.calls[tray.setContextMenu.mock.calls.length - 1][0];
    menu.handlers['menu-will-show']();
    const before = tray.setContextMenu.mock.calls.length;
    await controller.refresh();
    expect(tray.setContextMenu.mock.calls.length).toBe(before);
    menu.handlers['menu-will-close']();
    await vi.advanceTimersByTimeAsync(0);
    expect(tray.setContextMenu.mock.calls.length).toBe(before + 1);
    controller.stop();
  });

  /** The target line follows setTarget. */
  it('shows the target set by the window', () => {
    const { tray, controller } = setup();
    controller.setTarget('M 31');
    expect(lastItem(tray, 'Target: none')).toBeUndefined();
    expect(lastItem(tray, 'Target: M 31')).toBeTruthy();
    controller.stop();
  });

  /** Workspace items show the window and send the mode. */
  it('navigates the main window from a workspace item', async () => {
    const { tray, win, controller } = setup();
    await controller.refresh();
    lastItem(tray, 'Planetarium').click();
    expect(win.show).toHaveBeenCalled();
    expect(win.webContents.send).toHaveBeenCalledWith('navigate-mode', 'Planetarium');
    controller.stop();
  });

  /** Park uses the existing emergency park path. */
  it('parks through the emergency park path', async () => {
    const { tray, deps, controller } = setup();
    lastItem(tray, 'Park').click();
    await vi.waitFor(() => expect(deps.park).toHaveBeenCalledTimes(1));
    expect(deps.notify).not.toHaveBeenCalled();
    controller.stop();
  });

  /** A failed park shows a notice. */
  it('tells the user when park fails', async () => {
    const park = vi.fn().mockResolvedValue({ ok: false, error: 'no mount' });
    const { tray, deps, controller } = setup({ park });
    lastItem(tray, 'Park').click();
    await vi.waitFor(() => expect(deps.notify).toHaveBeenCalled());
    expect(deps.notify.mock.calls[0][1]).toContain('no mount');
    controller.stop();
  });

  /** Open App shows the window; Quit quits. */
  it('opens the app and quits', () => {
    const { tray, win, deps, controller } = setup();
    lastItem(tray, 'Open App').click();
    expect(win.show).toHaveBeenCalled();
    lastItem(tray, 'Quit').click();
    expect(deps.quit).toHaveBeenCalled();
    controller.stop();
  });

  /** Left click opens the menu on macOS and Windows, but not on Linux. */
  it('opens the menu on left click only off Linux', () => {
    const linux = setup();
    expect(linux.tray.on).not.toHaveBeenCalled();
    linux.controller.stop();
    const mac = setup({ platform: 'darwin' });
    expect(mac.tray.on).toHaveBeenCalledWith('click', expect.any(Function));
    mac.tray.on.mock.calls[0][1]();
    expect(mac.tray.popUpContextMenu).toHaveBeenCalled();
    mac.controller.stop();
  });
});
