/**
 * @fileoverview Electron preload script.
 * Exposes a minimal, safe API to the renderer via contextBridge to ensure
 * security and isolation between the main and renderer processes.
 */

/* Electron preload scripts run as CommonJS, so `require` is the only way to
   reach the electron module here. The previously named rules no longer fire
   under the current eslint config; `no-require-imports` is the one that does. */
/* eslint-disable @typescript-eslint/no-require-imports */
const { contextBridge, ipcRenderer } = require('electron');

/**
 * Public API exposed to the renderer at window.astrometrics.
 * Provides access to restricted main-process functionality.
 */
const api = {
	app: {
		/**
		 * Request the main process to load the target manager UI.
		 */
		loadTargetManager() {
			ipcRenderer.send('load-target-manager');
		},

		/**
		 * Subscribe to notifications from the main process to load the target manager.
		 * @param {Function} callback Function to call when the event is received.
		 * @returns {Function} Function to unsubscribe from the event.
		 */
		onLoadTargetManager(callback) {
			const handler = (event, ...args) => callback(event, ...args);
			ipcRenderer.on('load-target-manager', handler);
			return () => ipcRenderer.removeListener('load-target-manager', handler);
		},

		/**
		 * Toggle the secondary window on or off.
		 * @param {boolean} enable True to open, false to close.
		 */
		toggleSecondaryWindow(enable) {
			ipcRenderer.send('toggle-secondary-window', enable);
		},

		/**
		 * Subscribe to secondary window closed event (e.g. if user closed it manually).
		 */
		onSecondaryWindowClosed(callback) {
			const handler = (event, ...args) => callback(event, ...args);
			ipcRenderer.on('secondary-window-closed', handler);
			return () => ipcRenderer.removeListener('secondary-window-closed', handler);
		},

		/**
		 * Open a new display window with an optional initial workspace mode.
		 * @param {string|Object} [options] Requested mode string or options object.
		 * @returns {Promise<{ windowId: number } | null>}
		 */
		openDisplayWindow(options) {
			const payload = typeof options === 'string' ? { mode: options } : (options || {});
			return ipcRenderer.invoke('open-display-window', payload);
		},

		/**
		 * Report the current window's active workspace mode to the main process.
		 * @param {string} mode Current mode name.
		 */
		reportWindowMode(mode) {
			ipcRenderer.send('window-mode-changed', mode);
		},

		/**
		 * Route a cross-display navigation action to another window presenting the target display.
		 * @param {Object} intent The navigation intent object.
		 * @returns {Promise<{ handledRemotely: boolean, targetWindowId?: number }>}
		 */
		routeDisplayAction(intent) {
			return ipcRenderer.invoke('route-display-action', intent);
		},

		/**
		 * Subscribe to remote actions forwarded from other application windows.
		 * @param {Function} callback Receives { action, payload, intent }.
		 * @returns {Function} Unsubscribe function.
		 */
		onRemoteAction(callback) {
			const handler = (_event, data) => callback(data);
			ipcRenderer.on('remote-action', handler);
			return () => ipcRenderer.removeListener('remote-action', handler);
		},

		/**
		 * Show a native notification.
		 * @param {string} title
		 * @param {string} body
		 * @param {Object} [options]
		 * @param {'normal' | 'critical'} [options.urgency]
		 * @param {string} [options.tag]
		 * @param {boolean} [options.silent]
		 * @param {'default' | 'never'} [options.timeoutType]
		 * @param {string[]} [options.actions]
		 */
		showNotification(title, body, options = {}) {
			ipcRenderer.send('show-notification', { title, body, ...options });
		},

		/**
		 * Update dynamic tray menu with live mount status and active workspace.
		 * @param {Object} status
		 * @param {string} [status.mountStatus]
		 * @param {string} [status.activeTarget]
		 * @param {string} [status.activeMode]
		 */
		updateTrayStatus(status) {
			ipcRenderer.send('update-tray-status', status);
		},

		/**
		 * Enable or disable power-save blocker to prevent system sleep during imaging/guiding.
		 * @param {boolean} enable
		 */
		setPowerSaveBlocker(enable) {
			ipcRenderer.send('set-power-save-blocker', { enable });
		},

		/**
		 * Subscribe to mode navigation events triggered by OS jump lists, dock actions, or tray.
		 * @param {Function} callback (mode: string) => void
		 * @returns {Function} Unsubscribe function
		 */
		onNavigateMode(callback) {
			const handler = (_event, mode) => callback(mode);
			ipcRenderer.on('navigate-mode', handler);
			return () => ipcRenderer.removeListener('navigate-mode', handler);
		},

		/**
		 * Subscribe to OS theme change events (e.g. dark/light system mode toggle).
		 * @param {Function} callback (isDark: boolean) => void
		 * @returns {Function} Unsubscribe function
		 */
		onSystemThemeChanged(callback) {
			const handler = (_event, isDark) => callback(isDark);
			ipcRenderer.on('system-theme-changed', handler);
			return () => ipcRenderer.removeListener('system-theme-changed', handler);
		},

		/**
		 * Subscribe to notification action button clicks.
		 * @param {Function} callback ({ index: number }) => void
		 * @returns {Function} Unsubscribe function
		 */
		onNotificationAction(callback) {
			const handler = (_event, data) => callback(data);
			ipcRenderer.on('notification-action-clicked', handler);
			return () => ipcRenderer.removeListener('notification-action-clicked', handler);
		},

		/**
		 * Update taskbar progress.
		 * @param {number} progress 0-1
		 * @param {string} mode 'normal', 'error', 'none'
		 */
		setProgress(progress, mode) {
			ipcRenderer.send('set-progress', { progress, mode });
		},

		/**
		 * Subscribe to open-file request (from OS association).
		 * @param {Function} callback (path: string) => void
		 */
		onOpenFile(callback) {
			const handler = (event, path) => callback(path);
			ipcRenderer.on('open-file', handler);
			return () => ipcRenderer.removeListener('open-file', handler);
		}
	},
	backend: {
		/**
		 * Ping a backend URL (GET) and return the response status.
		 * @param {string} [targetUrl] Optional URL to ping; defaults to backend base.
		 * @returns {Promise<{ok:boolean, status:number, statusText:string}>}
		 */
		async ping(targetUrl) {
			return ipcRenderer.invoke('backend-ping', targetUrl);
		},

		/**
		 * Get the session token required by the backend's WebSocket endpoints.
		 * Supplied by the main process, which minted it and passed it to the
		 * backend on spawn; a file:// renderer cannot read the CORS-protected
		 * HTTP endpoint that serves the same value to browser clients.
		 * @returns {Promise<string>} The session token, or '' if unavailable.
		 */
		async sessionToken() {
			return ipcRenderer.invoke('backend-session-token');
		}
	},
	dialog: {
		/**
		 * Show an open-file dialog with the specified options.
		 * @param {Object} [options] Electron showOpenDialog options.
		 * @returns {Promise<string[]|null>} Resolves to file paths or null if cancelled.
		 */
		async openFile(options) {
			return ipcRenderer.invoke('dialog-open-file', options);
		}
	},
	tray: {
		/**
		 * Send a quick action from the tray popover to the main process.
		 * Supported actions: 'park', 'navigate', 'open-app', 'quit'.
		 * @param {string} action Action identifier.
		 * @param {Object} [payload] Optional action-specific payload (e.g. { mode: 'Planetarium' }).
		 */
		sendAction(action, payload) {
			ipcRenderer.send('tray-popover-action', { action, payload });
		},

		/**
		 * Subscribe to visibility changes of the tray popover window.
		 * Used to pause background animations and timers when hidden.
		 * @param {Function} callback (isVisible: boolean) => void
		 * @returns {Function} Unsubscribe function
		 */
		onVisibilityChange(callback) {
			const handler = (_event, isVisible) => callback(isVisible);
			ipcRenderer.on('tray-visibility-changed', handler);
			return () => ipcRenderer.removeListener('tray-visibility-changed', handler);
		}
	},
	terminal: {
		/**
		 * Execute Python script in supervised terminal environment.
		 * @param {string} code Python code snippet.
		 * @param {Object} [options] Execution options.
		 * @returns {Promise<Object>} Execution envelope with status, stdout, stderr, result, plots, workspace.
		 */
		async executeScript(code, options = {}) {
			return ipcRenderer.invoke('python-terminal-execute', { code, ...options });
		},

		/**
		 * Fetch current active workspace variable manifest.
		 * @returns {Promise<Array<Object>>}
		 */
		async getWorkspace() {
			return ipcRenderer.invoke('python-terminal-get-workspace');
		},

		/**
		 * Query completions for an input prefix.
		 * @param {string} text Prefix string.
		 * @returns {Promise<string[]>}
		 */
		async getCompletions(text) {
			return ipcRenderer.invoke('python-terminal-completions', { text });
		},

		/**
		 * Subscribe to streaming stdout/stderr chunks from execution.
		 * @param {Function} callback ({ stdout, stderr }) => void
		 * @returns {Function} Unsubscribe function
		 */
		onOutput(callback) {
			const handler = (_event, chunk) => callback(chunk);
			ipcRenderer.on('python-terminal-output', handler);
			return () => ipcRenderer.removeListener('python-terminal-output', handler);
		},

		/**
		 * Subscribe to figures/plots emitted by matplotlib.
		 * @param {Function} callback (plotPath: string) => void
		 * @returns {Function} Unsubscribe function
		 */
		onFigure(callback) {
			const handler = (_event, plotPath) => callback(plotPath);
			ipcRenderer.on('python-terminal-figure', handler);
			return () => ipcRenderer.removeListener('python-terminal-figure', handler);
		},

		/**
		 * Subscribe to workspace variable manifest updates.
		 * @param {Function} callback (workspace: Array<Object>) => void
		 * @returns {Function} Unsubscribe function
		 */
		onWorkspaceUpdated(callback) {
			const handler = (_event, workspace) => callback(workspace);
			ipcRenderer.on('python-terminal-workspace-updated', handler);
			return () => ipcRenderer.removeListener('python-terminal-workspace-updated', handler);
		}
	}
};

// Freeze surface to prevent tampering from renderer scripts.
Object.freeze(api);
Object.freeze(api.app);
Object.freeze(api.tray);
Object.freeze(api.terminal);

contextBridge.exposeInMainWorld('astrometrics', api);
