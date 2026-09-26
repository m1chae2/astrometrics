import { spawn } from 'child_process';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import os from 'os';
import log from 'electron-log';
import { getPlatform } from './platforms/index.js';

/**
 * Manages the backend process lifecycle.
 */
export class BackendManager {
  constructor(app, platform = getPlatform()) {
    this.app = app;
    this.platform = platform;
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

    /** Set once a backend this manager spawned has exited, so waiting on it can stop early. */
    this.backendExited = false;

    /**
     * Why the last `waitUntilWarm` call returned false: 'exited' (spawned backend died),
     * 'unreachable' (nothing answered for `unreachableTimeoutMs`) or 'timeout'
     * (answered but never reported ready). Null when it returned true.
     */
    this.warmUpFailureReason = null;
  }

  /**
   * Returns the environment for a spawned backend, carrying the session token
   * and enforcing OpenMP/BLAS thread quotas so heavy tasks like Siril stacking
   * and astrometry solvers do not consume 100% of host CPU cores.
   *
   * @return {NodeJS.ProcessEnv} Environment with ASTROMETRICS_SESSION_TOKEN and thread limits.
   */
  _backendEnv() {
    const totalCores = os.cpus()?.length || 4;
    // Cap thread usage to 75% of available cores, leaving at least 1-2 cores free for the OS and UI
    const maxWorkerThreads = String(Math.max(1, Math.min(totalCores - 1, Math.floor(totalCores * 0.75))));

    return {
      ...process.env,
      ASTROMETRICS_SESSION_TOKEN: this.sessionToken,
      OMP_NUM_THREADS: maxWorkerThreads,
      OPENBLAS_NUM_THREADS: maxWorkerThreads,
      MKL_NUM_THREADS: maxWorkerThreads,
      NUMEXPR_NUM_THREADS: maxWorkerThreads,
      VECLIB_MAXIMUM_THREADS: maxWorkerThreads
    };
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
    const workingDir = this.app.getAppPath();
    const env = this._backendEnv();

    const resolved = this.platform.resolveBackendExecutable(workingDir, !isDev);
    log.info(`Spawning backend: command=${resolved.command}, args=${JSON.stringify(resolved.args)}, maxThreads=${env.OMP_NUM_THREADS}`);

    const spawnCwd = resolved.isBinary ? path.dirname(resolved.command) : workingDir;
    this.backendProcess = spawn(resolved.command, resolved.args, {
      cwd: spawnCwd,
      stdio: 'pipe',
      env
    });

    if (this.backendProcess) {
      // Lower CPU scheduling priority of the backend process tree so telescope tracking
      // and UI event loops are prioritized by the OS scheduler ahead of heavy background compute
      if (this.backendProcess.pid) {
        try {
          os.setPriority(this.backendProcess.pid, os.constants.priority.PRIORITY_BELOW_NORMAL);
        } catch (err) {
          log.debug('Could not adjust backend process priority:', err);
        }
      }
      this._setupListeners(onReady);
    }
  }

  /**
   * Waits until the backend reports that its startup warm-up has finished.
   *
   * The backend starts accepting requests as soon as it is listening, but it
   * then loads the whole star catalog into memory, and until that is done the
   * first Planetarium load sits empty for tens of seconds. Polling here lets
   * the splash screen cover that time instead. Works whether this manager
   * spawned the backend or `SKIP_BACKEND` pointed it at one started elsewhere.
   *
   * Never blocks the app indefinitely: it gives up after `timeoutMs`, when a
   * spawned backend has exited, when nothing has answered at all for
   * `unreachableTimeoutMs` (no backend is running), or if the backend has no
   * `/api/ready` route (an older build). The reason for giving up is left in
   * `warmUpFailureReason` so the caller can tell a missing backend from a slow one.
   *
   * @param {{timeoutMs?: number, pollIntervalMs?: number, unreachableTimeoutMs?: number}} [options] - Polling limits.
   * @return {Promise<boolean>} True if the backend reported ready, false if it gave up waiting.
   */
  async waitUntilWarm({ timeoutMs = 180000, pollIntervalMs = 500, unreachableTimeoutMs = 45000 } = {}) {
    const port = process.env.ASTROMETRICS_PORT || '5000';
    const readyUrl = `http://127.0.0.1:${port}/api/ready`;
    const deadline = Date.now() + timeoutMs;
    let lastAnsweredAt = Date.now();
    this.warmUpFailureReason = null;

    while (Date.now() < deadline) {
      if (this.backendExited) {
        log.warn('Backend exited before finishing warm-up; not waiting any longer.');
        this.warmUpFailureReason = 'exited';
        return false;
      }
      try {
        const response = await fetch(readyUrl, { signal: AbortSignal.timeout(2000) });
        lastAnsweredAt = Date.now();
        if (response.ok) {
          log.info('Backend finished warm-up.');
          return true;
        }
        if (response.status === 404) {
          log.warn('Backend has no /api/ready route; not waiting for warm-up.');
          return true;
        }
      } catch {
        // Not listening yet, or too busy loading the catalog to answer in time; keep polling,
        // but give up if nothing has answered for so long that no backend is running.
        if (Date.now() - lastAnsweredAt >= unreachableTimeoutMs) {
          log.error(`No backend answered at ${readyUrl} for ${unreachableTimeoutMs}ms.`);
          this.warmUpFailureReason = 'unreachable';
          return false;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }

    log.warn(`Backend did not report ready within ${timeoutMs}ms; opening the window anyway.`);
    this.warmUpFailureReason = 'timeout';
    return false;
  }

  /**
   * Terminates the backend process.
   *
   * If the backend was spawned by this manager, terminates its process tree.
   * In addition, cleans up any external backend or Vite processes recorded in
   * `.run_pids/` so closing the desktop application leaves no orphaned child
   * or background services lingering.
   */
  stop() {
    if (this.backendProcess && !this.backendProcess.killed) {
      log.info('Terminating backend process tree...');
      this.platform.terminateProcessTree(this.backendProcess);
    }

    this._cleanupOrphanedRunPids();
  }

  /**
   * Reads PID files created by developer run scripts and terminates any running
   * backend or Vite dev processes before removing the PID files.
   *
   * @private
   */
  _cleanupOrphanedRunPids() {
    try {
      const appRoot = this.app?.getAppPath ? this.app.getAppPath() : process.cwd();
      const pidDir = path.join(appRoot, '.run_pids');
      if (!fs.existsSync(pidDir)) return;

      const pidFiles = ['backend.pid', 'vite.pid'];
      for (const file of pidFiles) {
        const filePath = path.join(pidDir, file);
        if (!fs.existsSync(filePath)) continue;

        try {
          const raw = fs.readFileSync(filePath, 'utf8').trim();
          const targetPid = parseInt(raw, 10);
          if (targetPid && !isNaN(targetPid)) {
            log.info(`Cleaning up background process from ${file} (pid: ${targetPid})...`);
            // Attempt to kill process group first, fallback to individual PID
            try {
              process.kill(-targetPid, 'SIGTERM');
            } catch {
              try {
                process.kill(targetPid, 'SIGTERM');
              } catch {
                // Already dead
              }
            }
          }
          fs.unlinkSync(filePath);
        } catch (err) {
          log.debug(`Failed to cleanly process ${filePath}:`, err);
        }
      }
    } catch (err) {
      log.debug('Failed to inspect .run_pids directory:', err);
    }
  }

  _getAppPath(...parts) {
    return path.join(this.app.getAppPath(), ...parts);
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
    this.backendProcess.on('close', (code) => {
      this.backendExited = true;
      log.info(`Backend exited with code ${code}`);
    });
  }
}
