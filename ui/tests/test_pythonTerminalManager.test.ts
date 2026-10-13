/**
 * @fileoverview Unit tests for the PythonTerminalManager.
 * Verifies RPC dispatch, output streaming, figure emission, workspace manifest handling,
 * autocompletions, and platform pause/resume integration.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { PythonTerminalManager } from '../../electron/python_terminal_manager.js';

describe('PythonTerminalManager', () => {
  let mockPlatform: any;
  let manager: PythonTerminalManager;
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    mockPlatform = {
      pauseBackgroundPipelines: vi.fn(),
      resumeBackgroundPipelines: vi.fn(),
      terminateProcessTree: vi.fn(),
    };

    manager = new PythonTerminalManager({
      backendUrl: 'http://127.0.0.1:5000',
      platform: mockPlatform,
    });

    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('initializes with default backend URL and unpaused state', () => {
    expect(manager.backendUrl).toBe('http://127.0.0.1:5000');
    expect(manager.isPaused).toBe(false);
    expect(manager.activeExecutions.size).toBe(0);
  });

  it('dispatches script execution and returns structured envelope with output streaming', async () => {
    const fakeEnvelope = {
      result: {
        data: {
          status: 'success',
          stdout: 'Calculated RA: 10.684\n',
          stderr: '',
          result: { ra: 10.684, dec: 41.269 },
          plots: ['/tmp/plot_test.png'],
          execution_time_ms: 45,
          workspace: [
            { name: 'm31', type: 'Target', shape: null, size_bytes: 1024, summary: 'Andromeda' },
          ],
        },
      },
    };

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => fakeEnvelope,
    } as any);

    const onOutput = vi.fn();
    const onFigure = vi.fn();

    const result = await manager.executeScript({
      code: 'result = {"ra": 10.684, "dec": 41.269}',
      onOutput,
      onFigure,
    });

    expect(globalThis.fetch).toHaveBeenCalledWith('http://127.0.0.1:5000/api/rpc', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }));

    expect(onOutput).toHaveBeenCalledWith({ stdout: 'Calculated RA: 10.684\n', stderr: '' });
    expect(onFigure).toHaveBeenCalledWith('/tmp/plot_test.png');
    expect(result.status).toBe('success');
    expect(result.result).toEqual({ ra: 10.684, dec: 41.269 });
    expect(result.workspace).toHaveLength(1);
    expect(result.workspace[0].name).toBe('m31');
  });

  it('fetches workspace variables manifest correctly', async () => {
    const fakeWorkspace = [
      { name: 'image', type: 'ndarray', shape: '(4096, 4096)', size_bytes: 33554432, summary: 'array' },
    ];

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: { data: fakeWorkspace } }),
    } as any);

    const vars = await manager.getWorkspaceVariables();
    expect(vars).toHaveLength(1);
    expect(vars[0].name).toBe('image');
    expect(vars[0].shape).toBe('(4096, 4096)');
  });

  it('fetches autocompletions for a code prefix', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: { data: ['astrometrics.targets', 'astrometrics.processing'] } }),
    } as any);

    const completions = await manager.getCompletions('astrometrics.');
    expect(completions).toEqual(['astrometrics.targets', 'astrometrics.processing']);
  });

  it('triggers platform adapter pause and resume hooks', () => {
    manager.pause();
    expect(manager.isPaused).toBe(true);
    expect(mockPlatform.pauseBackgroundPipelines).toHaveBeenCalledTimes(1);

    manager.resume();
    expect(manager.isPaused).toBe(false);
    expect(mockPlatform.resumeBackgroundPipelines).toHaveBeenCalledTimes(1);
  });

  it('clears active executions on stopAll', () => {
    manager.activeExecutions.add('job1');
    manager.activeExecutions.add('job2');
    expect(manager.activeExecutions.size).toBe(2);

    manager.stopAll();
    expect(manager.activeExecutions.size).toBe(0);
  });
});
