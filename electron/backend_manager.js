import { spawn } from 'child_process';
import crypto from 'crypto';
import path from 'path';
import fs from 'fs';
import os from 'os';
import log from 'electron-log';

/**
 * Manages the backend process lifecycle.
 */
export class BackendManager {
  constructor(app) {
    this.app = app;
    this.backendProcess = null;

    /**
     * Session token for the backend's WebSocket endpoints.
     *
     * Minted here rather than by the backend because the renderer may run
     * under `file://`, whose opaque origin cannot read the CORS-protected
     * `/api/session-token` endpoint. Generating it in the main process lets
     * it be passed down to the backend by environment variable and out to
     * the renderer over IPC, with neither path crossing a browser origin.
     */
    this.sessionToken = crypto.randomBytes(32).toString('base64url');
  }

  /**
   * Returns the environment for a spawned backend, carrying the session token.
   *
   * @return {NodeJS.ProcessEnv} Environment with ASTROMETRICS_SESSION_TOKEN set.
   */
  _backendEnv() {
    return { ...process.env, ASTROMETRICS_SESSION_TOKEN: this.sessionToken };
  }

  start(onReady) {
    if (process.env.SKIP_BACKEND) {
      log.info('SKIP_BACKEND is set; not starting backend.');
      // A backend started elsewhere mints its own session token, so the one
      // generated here was never handed to anything. Discard it rather than
      // let the renderer present a token no backend will accept; with none
      // offered over IPC the renderer falls back to /api/session-token, which
      // reports whatever the running backend actually holds.
      this.sessionToken = '';
      if (onReady) onReady();
      return;
    }

    log.info('Starting astrometrics-backend...');
    const isDev = !this.app.isPackaged;

    if (isDev) {
      log.info('Development mode: Spawning Python backend directly.');
      this.backendProcess = this._spawnPython();
    } else {
      log.info('Production mode: Searching for compiled backend binary...');
      const binaryName = os.platform() === 'win32' ? 'backend.exe' : 'backend';
      const possiblePaths = [
        this._getAppPath('dist', 'backend', binaryName),
        this._getAppPath('backend', 'dist', 'backend', binaryName),
        process.resourcesPath ? path.join(process.resourcesPath, 'app', 'dist', 'backend', binaryName) : null,
        process.resourcesPath ? path.join(process.resourcesPath, 'dist', 'backend', binaryName) : null,
      ].filter(p => typeof p === 'string');

      let compiledBackendPath = possiblePaths.find(p => fs.existsSync(p));

      if (compiledBackendPath) {
        this.backendProcess = this._spawnCompiled(compiledBackendPath);
      } else {
        log.warn('Packaged mode but no binary found! Falling back to Python.');
        this.backendProcess = this._spawnPython();
      }
    }

    if (this.backendProcess) {
      this._setupListeners(onReady);
    }
  }

  stop() {
    if (this.backendProcess && !this.backendProcess.killed) {
      log.info('Terminating backend process...');
      this.backendProcess.kill();
    }
  }

  _getAppPath(...parts) {
    return path.join(this.app.getAppPath(), ...parts);
  }

  _spawnCompiled(binaryPath) {
    log.info('Using compiled backend:', binaryPath);
    return spawn(binaryPath, [], {
      cwd: path.dirname(binaryPath),
      stdio: 'pipe',
      env: this._backendEnv()
    });
  }

  _spawnPython() {
    const workingDir = this.app.getAppPath();
    const env = this._backendEnv();
    if (os.platform() === 'win32') {
      const pythonPath = this._getAppPath('.venv', 'Scripts', 'python.exe');
      log.info('Using Python venv (Win):', pythonPath);
      return spawn(pythonPath, ['-m', 'backend.main_backend'], { cwd: workingDir, stdio: 'pipe', env });
    } else {
      const localPython = path.join(workingDir, '.venv', 'bin', 'python3');
      if (fs.existsSync(localPython)) {
        log.info('Using Python venv (Unix):', localPython);
        return spawn(localPython, ['-m', 'backend.main_backend'], { cwd: workingDir, stdio: 'pipe', env });
      }
      log.info('Spawning astrometrics-backend wrapper.');
      return spawn('astrometrics-backend', [], { cwd: workingDir, stdio: 'pipe', env });
    }
  }

  _setupListeners(onReady) {
    const checkReady = (data) => {
      const str = data.toString();
      if (str.includes('Application startup complete') || str.includes('Astrometrics Backend Running')) {
        log.info('Backend signaled readiness!');
        if (onReady) onReady();
      }
    };

    this.backendProcess.stdout.on('data', (data) => {
      log.info(`[Backend]: ${data}`);
      checkReady(data);
    });

    this.backendProcess.stderr.on('data', (data) => {
      log.error(`[Backend]: ${data}`);
      checkReady(data);
    });

    this.backendProcess.on('error', (err) => log.error('Backend process error:', err));
    this.backendProcess.on('close', (code) => log.info(`Backend exited with code ${code}`));
  }
}
