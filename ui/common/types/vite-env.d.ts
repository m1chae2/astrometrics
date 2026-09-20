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
      showNotification: (title: string, body: string, options?: { urgency?: 'normal' | 'critical'; actions?: string[] }) => void;
      updateTrayStatus: (status: { mountStatus?: string; activeTarget?: string; activeMode?: string }) => void;
      setPowerSaveBlocker: (enable: boolean) => void;
      onNavigateMode: (callback: (mode: string) => void) => () => void;
      onSystemThemeChanged: (callback: (isDark: boolean) => void) => () => void;
      onNotificationAction: (callback: (data: { index: number }) => void) => () => void;
      setProgress: (progress: number, mode: 'normal' | 'error' | 'none' | 'indeterminate' | 'paused') => void;
      onOpenFile: (callback: (path: string) => void) => () => void;
    };
    backend: {
      ping: (targetUrl?: string) => Promise<{ ok: boolean; status: number; statusText: string }>;
    };
    dialog: {
      openFile: (options?: any) => Promise<string[] | null>;
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
