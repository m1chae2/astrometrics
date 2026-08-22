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
		 * Show a native notification.
		 * @param {string} title
		 * @param {string} body
		 */
		showNotification(title, body) {
			ipcRenderer.send('show-notification', { title, body });
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
	}
};

// Freeze surface to prevent tampering from renderer scripts.
Object.freeze(api);
Object.freeze(api.app);

contextBridge.exposeInMainWorld('astrometrics', api);
