/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly MODE?: string;
  readonly DEV?: boolean;
  readonly PROD?: boolean;
  readonly VITE_BACKEND_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  astrometrics?: {
    app: {
      loadTargetManager: () => void;
      onLoadTargetManager: (callback: (event: any, ...args: any[]) => void) => () => void;
      toggleSecondaryWindow: (enable: boolean) => void;
      onSecondaryWindowClosed: (callback: (event: any, ...args: any[]) => void) => () => void;
      showNotification: (title: string, body: string, options?: { urgency?: 'normal' | 'critical'; tag?: string; silent?: boolean; timeoutType?: 'default' | 'never'; actions?: string[] }) => void;
      updateTrayStatus: (status: { mountStatus?: string; activeTarget?: string; activeMode?: string }) => void;
      setPowerSaveBlocker: (enable: boolean) => void;
      onNavigateMode: (callback: (mode: string) => void) => () => void;
      onSystemThemeChanged: (callback: (isDark: boolean) => void) => () => void;
      onNotificationAction: (callback: (data: { index: number }) => void) => () => void;
      setProgress: (progress: number, mode: 'normal' | 'error' | 'none' | 'indeterminate' | 'paused') => void;
      onOpenFile: (callback: (path: string) => void) => () => void;
      openDisplayWindow?: (options?: string | { mode?: string; displayIndex?: number }) => Promise<{ windowId: number } | null>;
      reportWindowMode?: (mode: string) => void;
      routeDisplayAction?: (intent: any) => Promise<{ handledRemotely: boolean; targetWindowId?: number }>;
      onRemoteAction?: (callback: (data: { action: string; payload: any; intent?: any }) => void) => () => void;
    };
    backend: {
      ping: (targetUrl?: string) => Promise<{ ok: boolean; status: number; statusText: string }>;
    };
    dialog: {
      openFile: (options?: any) => Promise<string[] | null>;
      saveFile?: (options?: any) => Promise<string | null>;
      openFigureWindow?: (plotPath: string) => Promise<{ windowId: number } | null>;
    };
    terminal?: {
      executeScript: (code: string, options?: any) => Promise<any>;
      getWorkspace: () => Promise<any[]>;
      getCompletions: (text: string) => Promise<string[]>;
      onOutput: (callback: (chunk: { stdout?: string; stderr?: string }) => void) => () => void;
      onFigure: (callback: (plotPath: string) => void) => () => void;
      onWorkspaceUpdated: (callback: (workspace: any[]) => void) => () => void;
    };
    tray: {
      /**
       * Dispatch a quick action from the tray popover to the Electron main process.
       * @param action - 'park' | 'navigate' | 'open-app' | 'quit'
       * @param payload - Optional action-specific payload, e.g. { mode: 'Planetarium' }
       */
      sendAction: (action: string, payload?: Record<string, string>) => void;
    };
  };
  electronAPI?: any; // Deprecated/Legacy check
}
