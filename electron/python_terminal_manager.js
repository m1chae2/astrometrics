/**
 * @fileoverview Python Terminal Execution Manager for Astrometrics.
 *
 * Supervises the interactive Python environment, connects to the running backend
 * container over JSON-RPC, streams output chunks, broadcasts workspace variable
 * manifests, and integrates with the host OS platform adapter and powerMonitor.
 * All backend requests go through `backend_client.js`.
 */

import log from 'electron-log';
import { callRpc } from './backend_client.js';
import { getPlatform } from './platforms/index.js';

/**
 * Runs Python code in the backend for the app's Python terminal, and pauses or
 * resumes background work when the computer sleeps or wakes.
 */
export class PythonTerminalManager {
  /**
   * @param {Object} [options]
   * @param {string} [options.backendUrl] Base URL of the Astrometrics backend.
   *   When left out, each request uses the address `ASTROMETRICS_PORT` names.
   * @param {BasePlatform} [options.platform] Platform adapter.
   */
  constructor({ backendUrl = null, platform = getPlatform() } = {}) {
    this.backendUrl = backendUrl ? backendUrl.replace(/\/$/, '') : null;
    this.platform = platform;
    this.activeExecutions = new Set();
    this.isPaused = false;
  }

  /**
   * Execute Python code in the supervised backend environment.
   *
   * @param {Object} options
   * @param {string} options.code Python code string to execute.
   * @param {Function} [options.onOutput] Callback receiving streamed output chunks.
   * @param {Function} [options.onFigure] Callback receiving figure file paths or base64.
   * @param {number} [options.timeoutMs] Optional timeout in milliseconds.
   * @returns {Promise<{
   *   status: string,
   *   stdout: string,
   *   stderr: string,
   *   result: any,
   *   plots: string[],
   *   execution_time_ms: number,
   *   workspace: Array<{name: string, type: string, shape: string|null, size_bytes: number, summary: string}>
   * }>}
   */
  async executeScript({ code, onOutput = null, onFigure = null, timeoutMs = 60000 } = {}) {
    const execId = Math.random().toString(36).substring(2, 9);
    this.activeExecutions.add(execId);

    const controller = new AbortController();
    const timeoutTimer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await callRpc('terminal:execute', { code_str: code }, {
        id: execId,
        signal: controller.signal,
        baseUrl: this.backendUrl || undefined,
      });

      if (!response.ok) {
        throw new Error(`Terminal execution failed with HTTP ${response.status}: ${response.statusText}`);
      }

      const envelope = response.envelope;
      if (!envelope) {
        throw new Error('Terminal execution failed: the backend reply was not JSON.');
      }
      const data = envelope.result?.data || envelope.result || {
        status: 'error',
        stdout: '',
        stderr: envelope.error?.message || 'Unknown RPC execution error',
        result: null,
        plots: [],
        execution_time_ms: 0,
        workspace: []
      };

      if (onOutput && (data.stdout || data.stderr)) {
        onOutput({ stdout: data.stdout, stderr: data.stderr });
      }

      if (onFigure && Array.isArray(data.plots) && data.plots.length > 0) {
        for (const plotPath of data.plots) {
          onFigure(plotPath);
        }
      }

      return data;
    } catch (err) {
      log.warn('Python execution error:', err);
      return {
        status: 'error',
        stdout: '',
        stderr: String(err),
        result: null,
        plots: [],
        execution_time_ms: 0,
        workspace: []
      };
    } finally {
      clearTimeout(timeoutTimer);
      this.activeExecutions.delete(execId);
    }
  }

  /**
   * Fetch the current active workspace variables manifest.
   *
   * @returns {Promise<Array<{name: string, type: string, shape: string|null, size_bytes: number, summary: string}>>}
   */
  async getWorkspaceVariables() {
    try {
      const response = await callRpc('terminal:get_workspace', {}, {
        id: 'workspace_poll',
        baseUrl: this.backendUrl || undefined,
      });

      if (!response.ok) return [];
      return response.envelope?.result?.data || [];
    } catch (err) {
      log.debug('Failed to fetch workspace manifest:', err);
      return [];
    }
  }

  /**
   * Request autocompletions for a code prefix in the terminal scope.
   *
   * @param {string} text Text prefix to complete.
   * @returns {Promise<string[]>}
   */
  async getCompletions(text) {
    try {
      const response = await callRpc('terminal:completions', { text }, {
        id: 'completions',
        baseUrl: this.backendUrl || undefined,
      });

      if (!response.ok) return [];
      return response.envelope?.result?.data || [];
    } catch (err) {
      log.debug('Failed to fetch completions:', err);
      return [];
    }
  }

  /**
   * Pause background compute and terminal worker execution.
   */
  pause() {
    this.isPaused = true;
    log.info('PythonTerminalManager: Pausing active execution pipelines.');
    this.platform.pauseBackgroundPipelines();
  }

  /**
   * Resume background compute and terminal worker execution.
   */
  resume() {
    this.isPaused = false;
    log.info('PythonTerminalManager: Resuming active execution pipelines.');
    this.platform.resumeBackgroundPipelines();
  }

  /**
   * Terminate all active jobs upon application quit.
   */
  stopAll() {
    this.activeExecutions.clear();
    log.info('PythonTerminalManager: Stopped all terminal tasks.');
  }
}
