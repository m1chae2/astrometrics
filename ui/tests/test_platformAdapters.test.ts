/**
 * @fileoverview Unit tests for the Electron OS Platform Adapters.
 * Verifies BasePlatform contracts, Windows 11 Jump Lists, taskbar coordinates,
 * blur dismissal, Linux Wayland decorators, and macOS menubar/dock behaviors.
 */

import fs from 'fs';
import path from 'path';
import { describe, it, expect, vi } from 'vitest';
import { BasePlatform } from '../../electron/platforms/base.js';
import { WindowsPlatform } from '../../electron/platforms/windows.js';
import { LinuxPlatform } from '../../electron/platforms/linux.js';
import { MacOSPlatform } from '../../electron/platforms/macos.js';
import { getPlatform } from '../../electron/platforms/index.js';

describe('Electron Platform Adapters', () => {
  describe('Platform Factory (getPlatform)', () => {
    /** Verifies correct subclass instantiation per process.platform override */
    it('instantiates appropriate platform adapter for each OS identifier', () => {
      expect(getPlatform('win32')).toBeInstanceOf(WindowsPlatform);
      expect(getPlatform('linux')).toBeInstanceOf(LinuxPlatform);
      expect(getPlatform('darwin')).toBeInstanceOf(MacOSPlatform);
      expect(getPlatform('unknown_os')).toBeInstanceOf(BasePlatform);
    });
  });

  describe('WindowsPlatform', () => {
    const platform = new WindowsPlatform();

    /** Verifies app identity configuration and the local (not roaming) Electron folder */
    it('sets app.name, app.setAppUserModelId and a local userData folder on Windows', () => {
      vi.stubEnv('LOCALAPPDATA', 'C:/Users/u/AppData/Local');
      const mockApp = {
        name: '',
        setAppUserModelId: vi.fn(),
        setPath: vi.fn(),
        getPath: vi.fn(() => 'C:/Users/u')
      } as any;

      platform.configureIdentity(mockApp);
      expect(mockApp.name).toBe('astrometrics');
      expect(mockApp.setAppUserModelId).toHaveBeenCalledWith('astrometrics');
      expect(mockApp.setPath).toHaveBeenCalledWith(
        'userData', path.join('C:/Users/u/AppData/Local', 'Astrometrics', 'Electron')
      );
      vi.unstubAllEnvs();
    });

    /** Verifies Electron's log sits next to the backend's logs in the state folder */
    it('writes the Electron log into the state folder on Windows', () => {
      vi.stubEnv('XDG_STATE_HOME', '');
      vi.stubEnv('LOCALAPPDATA', 'C:/Users/u/AppData/Local');
      const mockLog = { transports: { file: {} as any }, hooks: [] } as any;
      const mockApp = { getPath: vi.fn(() => 'C:/Users/u') } as any;

      const logPath = platform.configureLogging(mockApp, mockLog);
      expect(logPath).toBe(path.join('C:/Users/u/AppData/Local', 'Astrometrics', 'State', 'logs', 'main.log'));
      expect(mockLog.transports.file.resolvePathFn()).toBe(logPath);
      vi.unstubAllEnvs();
    });

    /** Verifies Windows user tasks (Jump List) registration */
    it('registers user tasks for Planetarium, Observatory, and Image Processing', () => {
      const mockApp = {
        setUserTasks: vi.fn(),
        getAppPath: vi.fn(() => 'C:/src/astrometrics')
      } as any;

      platform.setupUserTasks(mockApp);
      expect(mockApp.setUserTasks).toHaveBeenCalledTimes(1);
      const tasks = mockApp.setUserTasks.mock.calls[0][0];
      expect(tasks).toHaveLength(3);
      expect(tasks[0].title).toBe('Open Planetarium');
      expect(tasks[1].title).toBe('Observatory Manager');
      expect(tasks[2].title).toBe('Image Processing');
      // Each task runs the launcher with its workspace, not a bare electron.exe.
      const launcher = path.join('C:/src/astrometrics', 'build', 'windows', 'run_astrometrics.ps1');
      expect(tasks[0].program).toMatch(/powershell\.exe$/);
      expect(tasks[0].arguments).toContain(`-File "${launcher}"`);
      expect(tasks[0].arguments).toContain('-Mode "Planetarium"');
      expect(tasks[2].arguments).toContain('-Mode "Image Processing"');
    });

    /** Verifies the app asks Windows for the high-performance graphics card */
    it('asks for the high-performance graphics card on Windows', () => {
      const mockApp = { commandLine: { appendSwitch: vi.fn() } } as any;

      platform.initCommandLine(mockApp);
      expect(mockApp.commandLine.appendSwitch).toHaveBeenCalledWith('force_high_performance_gpu');
    });

    /** Verifies astrometrics:// links start the launcher, with the link as -Url */
    it('registers the launcher as the astrometrics:// handler on Windows', () => {
      const mockApp = {
        setAsDefaultProtocolClient: vi.fn(),
        getAppPath: vi.fn(() => 'C:/src/astrometrics')
      } as any;

      platform.registerProtocolClient(mockApp);
      const [scheme, program, args] = mockApp.setAsDefaultProtocolClient.mock.calls[0];
      expect(scheme).toBe('astrometrics');
      expect(program).toMatch(/powershell\.exe$/);
      expect(args).toContain(path.join('C:/src/astrometrics', 'build', 'windows', 'run_astrometrics.ps1'));
      expect(args[args.length - 1]).toBe('-Url');
    });

    /** Verifies notification option adapting for Windows Toast */
    it('adapts notification options and action buttons for Windows', () => {
      const mockApp = {
        getAppPath: () => '/mock/app'
      } as any;

      const adapted: any = platform.adaptNotificationOptions(
        { title: 'Telescope Alert', body: 'Guiding lost', tag: 'telescope-status', silent: true, actions: ['Acknowledge'] },
        mockApp
      );

      expect(adapted.title).toBe('Telescope Alert');
      expect(adapted.body).toBe('Guiding lost');
      expect(adapted.tag).toBe('telescope-status');
      expect(adapted.silent).toBe(true);
      expect(adapted.timeoutType).toBe('default');
      expect(adapted.actions).toEqual([{ type: 'button', text: 'Acknowledge' }]);
    });

    /** Verifies Window Controls Overlay configuration on Windows */
    it('returns Window Controls Overlay configuration on Windows', () => {
      const options = platform.getWindowOptions();
      expect(options.titleBarStyle).toBe('hidden');
      expect(options.titleBarOverlay).toEqual({
        color: '#242426',
        symbolColor: '#e0e0e0',
        height: 48
      });
    });

    /** Verifies the dev backend uses the Windows virtual environment that setup_windows.ps1 makes */
    it('starts the dev backend from .venv-win before .venv', () => {
      const exists = vi.spyOn(fs, 'existsSync').mockImplementation(() => true);
      try {
        const resolved = platform.resolveBackendExecutable('C:\\app', false);
        expect(resolved.command).toBe(path.join('C:\\app', '.venv-win', 'Scripts', 'python.exe'));
        expect(resolved.args).toEqual(['-m', 'backend.main_backend']);
        expect(resolved.isBinary).toBe(false);
      } finally {
        exists.mockRestore();
      }
    });

    /** Verifies a checkout with only a plain .venv still finds its Python */
    it('falls back to .venv when .venv-win is missing', () => {
      const exists = vi.spyOn(fs, 'existsSync').mockImplementation(
        candidate => !String(candidate).includes('.venv-win')
      );
      try {
        const resolved = platform.resolveBackendExecutable('C:\\app', false);
        expect(resolved.command).toBe(path.join('C:\\app', '.venv', 'Scripts', 'python.exe'));
      } finally {
        exists.mockRestore();
      }
    });
  });

  describe('LinuxPlatform', () => {
    const platform = new LinuxPlatform();

    /** Verifies the Wayland app_id is set apart from app.name (which names the user data folder) */
    it('sets the desktop name for the Wayland app_id and keeps app.name unchanged', () => {
      const mockApp = {
        name: '',
        setAppUserModelId: vi.fn(),
        setDesktopName: vi.fn()
      } as any;

      platform.configureIdentity(mockApp);
      expect(mockApp.name).toBe('astrometrics');
      expect(mockApp.setDesktopName).toHaveBeenCalledWith('io.github.m1chae2.Astrometrics.desktop');
    });

    /** Verifies Wayland window decorations switch */
    it('enables WaylandWindowDecorations switch on Linux', () => {
      const mockApp = {
        commandLine: {
          appendSwitch: vi.fn()
        }
      } as any;

      platform.initCommandLine(mockApp);
      expect(mockApp.commandLine.appendSwitch).toHaveBeenCalledWith(
        'ozone-platform-hint',
        'auto'
      );
      expect(mockApp.commandLine.appendSwitch).toHaveBeenCalledWith(
        'enable-features',
        'WaylandWindowDecorations,WebRTCPipeWireCapturer'
      );
    });

    /** Verifies Freedesktop notification urgency, tag, and timeoutType mapping */
    it('maps urgency levels and preserves tag and timeoutType for Freedesktop standards', () => {
      const mockApp = {
        getAppPath: () => '/mock/app'
      } as any;

      const critical: any = platform.adaptNotificationOptions(
        { title: 'Emergency', urgency: 'critical' as any, tag: 'telescope-status' },
        mockApp
      );
      expect(critical.urgency).toBe('critical');
      expect(critical.tag).toBe('telescope-status');
      expect(critical.timeoutType).toBe('never');

      const normal: any = platform.adaptNotificationOptions(
        { title: 'Connected', urgency: 'normal', tag: 'telescope-status', silent: true },
        mockApp
      );
      expect(normal.urgency).toBe('normal');
      expect(normal.tag).toBe('telescope-status');
      expect(normal.silent).toBe(true);
      expect(normal.timeoutType).toBe('default');

      const fallback: any = platform.adaptNotificationOptions(
        { title: 'Info', urgency: 'invalid' as any },
        mockApp
      );
      expect(fallback.urgency).toBe('normal');
      expect(fallback.timeoutType).toBe('default');
    });

    /** Verifies log rotation and size limit configuration */
    it('configures log file rotation with 5MB maxSize on Linux', () => {
      const mockApp = {
        getPath: vi.fn().mockReturnValue('/home/testuser')
      } as any;
      const mockLog = {
        transports: {
          file: {
            level: 'info',
            maxSize: 0,
            resolvePathFn: null as any
          }
        }
      };

      const logPath = platform.configureLogging(mockApp, mockLog);
      expect(mockLog.transports.file.maxSize).toBe(5 * 1024 * 1024);
      expect(logPath).toContain('astrometrics');
    });

    /** Verifies application menu accelerator configuration */
    it('builds standard application menu with CmdOrCtrl accelerators and F11', () => {
      const mockApp = { quit: vi.fn() } as any;
      const mockMenuModule = {
        buildFromTemplate: vi.fn((tmpl) => ({ template: tmpl })),
        setApplicationMenu: vi.fn()
      };
      const onOpenFile = vi.fn();

      const menu = platform.setupApplicationMenu(mockApp, {
        onOpenFile,
        isDev: true,
        menuModule: mockMenuModule as any
      });

      expect(mockMenuModule.buildFromTemplate).toHaveBeenCalledTimes(1);
      expect(mockMenuModule.setApplicationMenu).toHaveBeenCalledTimes(1);

      const template = (menu as any).template;
      const fileMenu = template.find((m: any) => m.label === 'File');
      expect(fileMenu).toBeDefined();

      const openItem = fileMenu.submenu.find((i: any) => i.label === 'Open FITS File...');
      expect(openItem.accelerator).toBe('CmdOrCtrl+O');
      openItem.click();
      expect(onOpenFile).toHaveBeenCalledTimes(1);

      const viewMenu = template.find((m: any) => m.label === 'View');
      const fullscreenItem = viewMenu.submenu.find((i: any) => i.accelerator === 'F11');
      expect(fullscreenItem).toBeDefined();
    });

    /** Verifies background pipeline pause/resume via SIGSTOP and SIGCONT */
    it('executes SIGSTOP and SIGCONT commands for pipeline pause and resume', () => {
      const mockExec = vi.fn();

      const pauseResult = platform.pauseBackgroundPipelines({ execFn: mockExec });
      expect(pauseResult).toBe(true);
      expect(mockExec).toHaveBeenCalledWith('pkill -STOP -f siril-cli || true', expect.any(Function));
      expect(mockExec).toHaveBeenCalledWith('pkill -STOP -f solve-field || true', expect.any(Function));

      const resumeResult = platform.resumeBackgroundPipelines({ execFn: mockExec });
      expect(resumeResult).toBe(true);
      expect(mockExec).toHaveBeenCalledWith('pkill -CONT -f siril-cli || true', expect.any(Function));
      expect(mockExec).toHaveBeenCalledWith('pkill -CONT -f solve-field || true', expect.any(Function));
    });
  });

  describe('MacOSPlatform', () => {
    const platform = new MacOSPlatform();

    /** Verifies macOS window all closed behavior */
    it('does not quit application when all windows are closed on macOS', () => {
      expect(platform.shouldQuitOnWindowAllClosed()).toBe(false);
    });

    /** Verifies macOS application menu structure */
    it('builds macOS menu with application and edit menus', () => {
      const mockApp = { name: 'Astrometrics', quit: vi.fn() } as any;
      const mockMenuModule = {
        buildFromTemplate: vi.fn((tmpl) => ({ template: tmpl })),
        setApplicationMenu: vi.fn()
      };

      const menu = platform.setupApplicationMenu(mockApp, {
        menuModule: mockMenuModule as any
      });

      expect(mockMenuModule.buildFromTemplate).toHaveBeenCalledTimes(1);
      const template = (menu as any).template;
      expect(template[0].label).toBe('Astrometrics');
      expect(template[2].label).toBe('Edit');
    });

    /** Verifies notification option adapting on macOS */
    it('adapts notification options and preserves tag and timeoutType for macOS', () => {
      const mockApp = {} as any;
      const adapted: any = platform.adaptNotificationOptions(
        { title: 'Telescope Alert', body: 'Guiding lost', tag: 'telescope-status', silent: true, actions: ['Acknowledge'] },
        mockApp
      );
      expect(adapted.title).toBe('Telescope Alert');
      expect(adapted.body).toBe('Guiding lost');
      expect(adapted.tag).toBe('telescope-status');
      expect(adapted.silent).toBe(true);
      expect(adapted.timeoutType).toBe('default');
    });
  });
});
