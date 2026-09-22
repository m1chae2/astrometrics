/**
 * @fileoverview Unit tests for the Electron OS Platform Adapters.
 * Verifies BasePlatform contracts, Windows 11 Jump Lists, taskbar coordinates,
 * blur dismissal, Linux Wayland decorators, and macOS menubar/dock behaviors.
 */

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

    /** Verifies app identity configuration */
    it('sets app.name and app.setAppUserModelId on Windows', () => {
      const mockApp = {
        name: '',
        setAppUserModelId: vi.fn()
      } as any;

      platform.configureIdentity(mockApp);
      expect(mockApp.name).toBe('astrometrics');
      expect(mockApp.setAppUserModelId).toHaveBeenCalledWith('astrometrics');
    });

    /** Verifies Windows user tasks (Jump List) registration */
    it('registers user tasks for Planetarium, Observatory, and Image Processing', () => {
      const mockApp = {
        setUserTasks: vi.fn()
      } as any;

      platform.setupUserTasks(mockApp);
      expect(mockApp.setUserTasks).toHaveBeenCalledTimes(1);
      const tasks = mockApp.setUserTasks.mock.calls[0][0];
      expect(tasks).toHaveLength(3);
      expect(tasks[0].title).toBe('Open Planetarium');
      expect(tasks[1].title).toBe('Observatory Manager');
      expect(tasks[2].title).toBe('Image Processing');
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

    /** Verifies blur dismissal behavior on Windows */
    it('enables blur dismissal for Windows 11', () => {
      expect(platform.shouldDismissTrayOnBlur()).toBe(true);
    });

    /** Verifies tray popover position calculation for bottom taskbar */
    it('anchors popover directly above bottom taskbar tray icon clamped within workArea', () => {
      const mockTray = {
        getBounds: () => ({ x: 1500, y: 1040, width: 32, height: 40 })
      } as any;

      const mockScreen = {
        getDisplayNearestPoint: () => ({
          workArea: { x: 0, y: 0, width: 1920, height: 1040 }
        })
      };

      const position = platform.getTrayPopoverPosition(
        mockTray,
        { width: 340, height: 460 },
        mockScreen as any
      );

      expect(position).not.toBeNull();
      // x should be centered on tray icon (1500 + 16 - 170 = 1346)
      expect(position?.x).toBe(1346);
      // y should sit right above taskbar (1040 - 460 = 580)
      expect(position?.y).toBe(580);
    });

    /** Verifies tray popover clamping if tray icon is at screen edge */
    it('clamps popover inside workArea boundaries if tray icon is at screen edge', () => {
      const mockTray = {
        getBounds: () => ({ x: 1910, y: 1040, width: 10, height: 40 })
      } as any;

      const mockScreen = {
        getDisplayNearestPoint: () => ({
          workArea: { x: 0, y: 0, width: 1920, height: 1040 }
        })
      };

      const position = platform.getTrayPopoverPosition(
        mockTray,
        { width: 340, height: 460 },
        mockScreen as any
      );

      expect(position).not.toBeNull();
      // x must not exceed workArea width (1920 - 340 = 1580)
      expect(position?.x).toBe(1580);
    });

    /** Verifies Window Controls Overlay configuration on Windows */
    it('returns Window Controls Overlay configuration on Windows', () => {
      const options = platform.getWindowOptions();
      expect(options.titleBarStyle).toBe('hidden');
      expect(options.titleBarOverlay).toEqual({
        color: '#181818',
        symbolColor: '#e0e0e0',
        height: 48
      });
    });
  });

  describe('LinuxPlatform', () => {
    const platform = new LinuxPlatform();

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

    /** Verifies Wayland floating window and blur behavior */
    it('returns null for tray positioning and disables blur dismiss on Wayland', () => {
      expect(platform.getTrayPopoverPosition()).toBeNull();
      expect(platform.shouldDismissTrayOnBlur()).toBe(false);
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

    /** Verifies top menubar popover positioning */
    it('positions tray popover directly below top menubar status item', () => {
      const mockTray = {
        getBounds: () => ({ x: 1200, y: 0, width: 24, height: 24 })
      } as any;

      const mockScreen = {
        getDisplayNearestPoint: () => ({
          workArea: { x: 0, y: 24, width: 1440, height: 876 }
        })
      };

      const position = platform.getTrayPopoverPosition(
        mockTray,
        { width: 340, height: 460 },
        mockScreen as any
      );

      expect(position).not.toBeNull();
      expect(position?.y).toBe(24);
      expect(platform.shouldDismissTrayOnBlur()).toBe(true);
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
