/**
 * Purpose: Start one of the project's Python MCP servers on Windows or Linux.
 *
 * Description: The MCP client configs (.mcp.json and the others written by
 * build/mcp/generate_client_configs.py) cannot name the Python program
 * directly, because it lives in a different place on each system: the Linux
 * virtual environment is `.venv/bin/python` and the Windows one is
 * `.venv-win\Scripts\python.exe`. The configs start this script with Node
 * (already required by the project), and it finds the right Python, runs the
 * module it was given with `python -m`, and passes on the exit code.
 *
 * Usage: node build/mcp/run_python_server.mjs <module>
 *   e.g. node build/mcp/run_python_server.mjs mcp_servers.backend
 *
 * Set ASTROMETRICS_PYTHON to the full path of a Python program to use that
 * one instead of the virtual environment's.
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** The project folder: two levels above this file (build/mcp). */
const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * List where the project's Python program may be, best choice first.
 *
 * @param {string} platform - The value of `process.platform`.
 * @returns {string[]} Full paths to try in order.
 */
export function pythonCandidates(platform) {
  if (platform === 'win32') {
    return [
      path.join(PROJECT_ROOT, '.venv-win', 'Scripts', 'python.exe'),
      path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe'),
    ];
  }
  return [path.join(PROJECT_ROOT, '.venv', 'bin', 'python')];
}

/**
 * Find the Python program to run the server with.
 *
 * @param {string} platform - The value of `process.platform`.
 * @param {NodeJS.ProcessEnv} environment - The environment variables.
 * @returns {string | null} The full path, or `null` if none exists.
 */
export function findPython(platform, environment) {
  const chosen = environment.ASTROMETRICS_PYTHON;
  if (chosen) {
    return chosen;
  }
  return pythonCandidates(platform).find((candidate) => existsSync(candidate)) ?? null;
}

/**
 * Start the server module and wait for it to end.
 *
 * @param {string[]} argumentsList - The command-line arguments after the script name.
 * @returns {number | null} An exit code if the server could not be started, else `null`.
 */
function main(argumentsList) {
  const [moduleName, ...extraArguments] = argumentsList;
  if (!moduleName) {
    console.error('Usage: node build/mcp/run_python_server.mjs <module>');
    return 2;
  }
  const python = findPython(process.platform, process.env);
  if (!python) {
    console.error(
      `No Python environment found. Looked for: ${pythonCandidates(process.platform).join(', ')}. ` +
        'Run build/windows/setup_windows.ps1 (Windows) or build/linux/setup_venv.sh (Linux).',
    );
    return 1;
  }
  const child = spawn(python, ['-m', moduleName, ...extraArguments], {
    cwd: PROJECT_ROOT,
    stdio: 'inherit',
    env: {
      PYTHONPATH: PROJECT_ROOT,
      // Matplotlib needs a folder it can write to; the system's temp folder differs by OS.
      MPLCONFIGDIR: path.join(os.tmpdir(), 'matplotlib'),
      PYTHONUTF8: '1',
      ...process.env,
    },
  });
  child.on('error', (error) => {
    console.error(`Could not start ${python}: ${error.message}`);
    process.exit(1);
  });
  child.on('exit', (code, signal) => {
    process.exit(code ?? (signal ? 1 : 0));
  });
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => child.kill(signal));
  }
  return null;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const failure = main(process.argv.slice(2));
  if (failure !== null) {
    process.exit(failure);
  }
}
