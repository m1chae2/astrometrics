/**
 * @file test_commandConsole.test.tsx
 * @description Unit tests for the CommandConsole component, validating 3-column workbench
 * rendering, sidebar tab switching, REPL execution, and metrics scorecard display.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CommandConsole } from '../commandConsole/CommandConsole';
import * as backendApi from '../common/services/backendApi';

// Mock react-resizable-panels to render lightweight layout containers in JSDOM
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: React.ReactNode }) => <div data-testid="panel-group">{children}</div>,
  Panel: ({ children }: { children: React.ReactNode }) => <div data-testid="panel">{children}</div>,
  Separator: () => <div data-testid="separator" />,
}));

// Mock CodeMirror editor component
vi.mock('../commandConsole/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onRun,
    filename,
  }: {
    value: string;
    onRun: () => void;
    filename: string;
  }) => (
    <div data-testid="code-editor">
      <span data-testid="editor-filename">{filename}</span>
      <textarea data-testid="editor-textarea" value={value} readOnly />
      <button data-testid="editor-run-btn" onClick={onRun}>
        Run Script
      </button>
    </div>
  ),
}));

// Mock react-markdown
vi.mock('react-markdown', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="markdown-body">{children}</div>,
}));

describe('CommandConsole Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Test basic mounting and initial backend queries for recipes, scripts, and workspace variables.
   */
  it('renders workbench columns and fetches initial state', async () => {
    const mockCallBackend = vi.spyOn(backendApi, 'callBackend').mockImplementation(async (method) => {
      if (method === 'terminal:list_recipes') {
        return [
          {
            id: 'photometry_curve',
            title: 'Extract Light Curve',
            description: 'Calculates differential photometry',
            category: 'photometry',
            code: 'print("light curve")',
          },
        ];
      }
      if (method === 'terminal:list_scripts') {
        return [
          {
            name: 'custom_analysis.py',
            path: '/path/to/custom_analysis.py',
            size_bytes: 120,
            modified_time: 1727100000,
          },
        ];
      }
      if (method === 'processing:list_jobs') {
        return [
          {
            id: 'job-101',
            targetId: 'M31',
            pipelineType: 'Stacking',
            status: 'Completed',
            currentStep: 'Done',
            progress: 100,
            inputMetrics: { frame_count: 20, mean_snr: 18.5 },
            outputMetrics: { stacked_fwhm: 2.1, roundness: 0.94 },
          },
        ];
      }
      if (method === 'terminal:get_workspace') {
        return [
          {
            name: 'targets',
            type: 'TargetDatabaseDriver',
            shape: null,
            dtype: null,
            size_bytes: null,
            is_pinned: true,
            description: 'Target Catalog Driver',
          },
          {
            name: 'snr_array',
            type: 'ndarray',
            shape: [20, 20],
            dtype: 'float64',
            size_bytes: 3200,
            is_pinned: false,
            description: null,
          },
        ];
      }
      if (method === 'docs:list_topics') {
        return [
          {
            id: 'terminal_usage',
            title: 'Terminal & Scripting Guide',
            path: 'documentation/terminal_usage.md',
            category: 'Developer Guide',
          },
        ];
      }
      return null;
    });

    render(<CommandConsole />);

    await waitFor(() => {
      expect(mockCallBackend).toHaveBeenCalledWith('terminal:list_recipes', {});
      expect(mockCallBackend).toHaveBeenCalledWith('terminal:list_scripts', {});
      expect(mockCallBackend).toHaveBeenCalledWith('processing:list_jobs', { limit: 25 });
      expect(mockCallBackend).toHaveBeenCalledWith('terminal:get_workspace', {});
    });

    // Check editor is mounted
    expect(screen.getByTestId('code-editor')).toBeInTheDocument();

    // Check terminal REPL banner exists
    expect(screen.getAllByText(/Astrometrics Command Console/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Scientific environment connected to observatory hardware/i)).toBeInTheDocument();
  });

  /**
   * Test switching tabs to pipeline runs and viewing metrics scorecard.
   */
  it('allows selecting a pipeline run and switching to the Metrics Scorecard tab', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation(async (method) => {
      if (method === 'processing:list_jobs') {
        return [
          {
            id: 'job-999',
            targetId: 'NGC7000',
            pipelineType: 'Photometry',
            jobType: 'Photometry',
            status: 'Completed',
            currentStep: 'Done',
            progress: 100,
            inputMetrics: { frame_count: 15, mean_fwhm: 3.2 },
            outputMetrics: { target_magnitude: 11.4, error: 0.03 },
          },
        ];
      }
      if (method === 'terminal:get_workspace') {
        return [];
      }
      return [];
    });

    render(<CommandConsole />);

    // Switch sidebar tab to "Pipeline Runs"
    const runsTabBtn = await screen.findByRole('button', { name: /Pipeline Runs/i });
    fireEvent.click(runsTabBtn);

    // Verify run is visible in sidebar
    const runItem = await screen.findByText('NGC7000');
    expect(runItem).toBeInTheDocument();

    // Click run item to select it
    fireEvent.click(runItem);

    // Switch right panel tab to "Metrics Scorecard"
    const metricsTabBtn = screen.getByRole('button', { name: /Metrics Scorecard/i });
    fireEvent.click(metricsTabBtn);

    // Verify scorecard displays metrics
    expect(await screen.findByText(/Run Metrics: NGC7000/i)).toBeInTheDocument();
    expect(screen.getByText('FRAME COUNT')).toBeInTheDocument();
    expect(screen.getByText('TARGET MAGNITUDE')).toBeInTheDocument();
  });

  /**
   * Test executing commands through the REPL pane.
   */
  it('submits code to terminal REPL and receives output', async () => {
    const mockCallBackend = vi.spyOn(backendApi, 'callBackend').mockImplementation(async (method, params) => {
      if (method === 'terminal:execute') {
        return {
          status: 'success',
          stdout: '42\n',
          stderr: '',
          plot_path: null,
          error: null,
        };
      }
      if (method === 'terminal:get_workspace') {
        return [];
      }
      return [];
    });

    render(<CommandConsole />);

    const replInput = screen.getByPlaceholderText(/Type Python expression or command/i);
    fireEvent.change(replInput, { target: { value: '6 * 7' } });
    fireEvent.keyDown(replInput, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(mockCallBackend).toHaveBeenCalledWith('terminal:execute', {
        code_str: '6 * 7',
        source: 'repl',
      });
    });

    // Verify REPL output display
    expect(await screen.findByText('42')).toBeInTheDocument();
  });
});
