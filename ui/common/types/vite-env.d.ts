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
      showNotification: (title: string, body: string) => void;
      setProgress: (progress: number, mode: 'normal' | 'error' | 'none' | 'indeterminate' | 'paused') => void;
      onOpenFile: (callback: (path: string) => void) => () => void;
    };
    backend: {
      ping: (targetUrl?: string) => Promise<{ ok: boolean; status: number; statusText: string }>;
    };
    dialog: {
      openFile: (options?: any) => Promise<string[] | null>;
    };
  };
  electronAPI?: any; // Deprecated/Legacy check
}
