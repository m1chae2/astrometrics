/**
 * @file test_commandConsole.test.tsx
 * @description Unit tests for the CommandConsole display: the first reads, the one script list
 * (your scripts and the examples), opening and saving scripts, the folding left column, the
 * terminal (its running line, Ctrl+C, clear and doc), show requests, and events from AI agents.
 */

import React, { Activity } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { postHandoff } from '../common/utils/displayHandoffStore';
import { COMMAND_CONSOLE_EVENT_KIND } from '../common/utils/commandConsoleEvents';
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
  }: {
    value: string;
    onRun: () => void;
  }) => (
    <div data-testid="code-editor">
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
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  /**
   * Test basic mounting and initial backend queries for notebooks, scripts, and workspace variables.
   */
  it('renders workbench columns and fetches initial state', async () => {
    const mockCallBackend = vi.spyOn(backendApi, 'callBackend').mockImplementation(async (method) => {
      if (method === 'terminal:list_recipes') {
        return [
          {
            id: 'photometry_curve',
            name: 'Extract Light Curve',
            filename: 'photometry_curve.ipynb',
            description: 'Calculates differential photometry',
            category: 'astrometrics',
            kind: 'notebook',
            size_bytes: 10,
          },
        ];
      }
      if (method === 'terminal:list_scripts') {
        return [
          {
            id: 'custom_analysis',
            name: 'Custom Analysis',
            filename: 'custom_analysis.py',
            size_bytes: 120,
            modified_at: 1727100000,
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
      expect(mockCallBackend).toHaveBeenCalledWith('terminal:get_workspace', {});
    });

    // Check editor is mounted
    expect(screen.getByTestId('code-editor')).toBeInTheDocument();

    // Your scripts and the examples are on two tabs of one list.
    expect(await screen.findByText('Custom Analysis')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'My scripts (1)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Examples (1)' }));
    expect(await screen.findByText('Extract Light Curve')).toBeInTheDocument();
    expect(screen.getByText('Astrometrics')).toBeInTheDocument();
    // A notebook row shows its file extension as a badge in place of a second line.
    expect(screen.getByTitle('Jupyter notebook')).toHaveTextContent('.ipynb');

    // The terminal starts with one short line about its commands.
    expect(screen.getByText(/type help for the preloaded names/)).toBeInTheDocument();
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
      }, { timeoutMs: 0 });
    });

    // Verify REPL output display
    expect(await screen.findByText('42')).toBeInTheDocument();
  });

  /**
   * Terminal output an AI agent produces while the console is hidden and paused
   * waits in the hand-off store and shows, in order, once the console is shown.
   */
  it('shows agent terminal output that arrived while the console was paused', async () => {
    vi.spyOn(backendApi, 'callBackend').mockResolvedValue([] as never);
    const { rerender } = render(
      <Activity mode="hidden">
        <CommandConsole />
      </Activity>
    );
    act(() => {
      postHandoff(
        'Command Console',
        COMMAND_CONSOLE_EVENT_KIND,
        { action: 'terminal:agent_exec', payload: { code: 'print("first agent line")' } },
        { queue: true }
      );
      postHandoff(
        'Command Console',
        COMMAND_CONSOLE_EVENT_KIND,
        { action: 'terminal:agent_exec', payload: { code: 'print("second agent line")' } },
        { queue: true }
      );
    });
    rerender(
      <Activity mode="visible">
        <CommandConsole />
      </Activity>
    );
    const first = await screen.findByText('print("first agent line")');
    const second = await screen.findByText('print("second agent line")');
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  /**
   * A `scripts:changed` event (a script saved by an AI agent or another window)
   * makes the console load the User Scripts list again.
   */
  it('reloads the user scripts list when a scripts:changed event arrives', async () => {
    let scripts: Array<Record<string, unknown>> = [];
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:list_scripts') return scripts;
      return [];
    }) as never);
    render(<CommandConsole />);
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:list_scripts', {}));
    expect(screen.queryByText('Fresh Script')).not.toBeInTheDocument();

    scripts = [
      { id: 'fresh_script', name: 'Fresh Script', filename: 'fresh_script.py', size_bytes: 8, modified_at: 1 },
    ];
    act(() => {
      postHandoff(
        'Command Console',
        COMMAND_CONSOLE_EVENT_KIND,
        { action: 'scripts:changed', payload: { filename: 'fresh_script.py' } },
        { queue: true }
      );
    });
    expect(await screen.findByText('Fresh Script')).toBeInTheDocument();
  });

  /**
   * A run from the editor is named after the open script, so the run history
   * shows the script's file name. The unsaved scratch buffer sends no label.
   */
  it('passes the open script name as the label when the editor runs', async () => {
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:list_scripts') {
        return [{ id: 'fresh', name: 'Fresh', filename: 'fresh_script.py', size_bytes: 8, modified_at: 1 }];
      }
      if (method === 'terminal:read_script') return { filename: 'fresh_script.py', code: 'print(1)' };
      if (method === 'terminal:execute') {
        return { status: 'success', stdout: '', stderr: '', plots: [], interactive_plots: [], execution_time_ms: 1, workspace: [] };
      }
      return [];
    }) as never);
    render(<CommandConsole />);

    fireEvent.click(screen.getByTestId('editor-run-btn'));
    await waitFor(() =>
      expect(callSpy).toHaveBeenCalledWith('terminal:execute', expect.objectContaining({ source: 'editor' }), { timeoutMs: 0 })
    );
    const scratchCall = callSpy.mock.calls.find(([method]) => method === 'terminal:execute');
    expect(scratchCall?.[1]).not.toHaveProperty('label');

    fireEvent.click(await screen.findByText('Fresh'));
    await waitFor(() => expect(screen.getByLabelText('Script name')).toHaveValue('fresh_script.py'));
    fireEvent.click(screen.getByTestId('editor-run-btn'));
    await waitFor(() =>
      expect(callSpy).toHaveBeenCalledWith('terminal:execute', {
        code_str: 'print(1)',
        source: 'editor',
        label: 'fresh_script.py',
      }, { timeoutMs: 0 })
    );
  });

  /**
   * Opening a notebook puts its code in the editor with a ".py" name. Saving it writes your own
   * script under that name, and the script then shows as open in the list.
   */
  it('saves an opened notebook as your own script', async () => {
    let scripts: Array<Record<string, unknown>> = [];
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string, params: any) => {
      if (method === 'terminal:list_recipes') {
        return [{ id: 'light_curve', name: 'Light Curve', filename: 'light_curve.ipynb', category: 'astrometrics', kind: 'notebook', description: '', size_bytes: 1 }];
      }
      if (method === 'terminal:list_scripts') return scripts;
      if (method === 'terminal:get_recipe') return { id: 'light_curve', filename: 'light_curve.py', code: 'print("curve")' };
      if (method === 'terminal:save_script') {
        scripts = [{ id: 'light_curve', name: 'Light Curve', filename: params.filename, size_bytes: 14, modified_at: 2 }];
        return { filename: params.filename, size_bytes: 14, status: 'saved' };
      }
      return [];
    }) as never);
    render(<CommandConsole />);

    fireEvent.click(await screen.findByRole('tab', { name: 'Examples (1)' }));
    fireEvent.click(await screen.findByText('Light Curve'));
    await waitFor(() => expect(screen.getByLabelText('Script name')).toHaveValue('light_curve.py'));
    expect(screen.getByTestId('editor-textarea')).toHaveValue('print("curve")');

    fireEvent.click(screen.getByRole('button', { name: 'Save as My Script' }));
    await waitFor(() =>
      expect(callSpy).toHaveBeenCalledWith('terminal:save_script', { filename: 'light_curve.py', content: 'print("curve")' })
    );
    // The saved script shows on the My scripts tab, which opens by itself.
    expect(await screen.findByText('light_curve.py')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'My scripts (1)' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  /**
   * Saving under the name of another script you already have asks first, and nothing is
   * written until you confirm.
   */
  it('asks before replacing another script of the same name', async () => {
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string, params: any) => {
      if (method === 'terminal:list_scripts') {
        return [{ id: 'taken', name: 'Taken', filename: 'taken.py', size_bytes: 1, modified_at: 1 }];
      }
      if (method === 'terminal:save_script') return { filename: params.filename, size_bytes: 1, status: 'saved' };
      return [];
    }) as never);
    render(<CommandConsole />);
    await screen.findByText('Taken');

    fireEvent.change(screen.getByLabelText('Script name'), { target: { value: 'taken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('dialog', { name: 'Replace script?' })).toBeInTheDocument();
    expect(callSpy).not.toHaveBeenCalledWith('terminal:save_script', expect.anything());

    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    await waitFor(() =>
      expect(callSpy).toHaveBeenCalledWith('terminal:save_script', expect.objectContaining({ filename: 'taken.py' }))
    );
  });

  /**
   * The script list folds to a thin strip that names the open script, like the other
   * displays' left columns, and opens again from the strip.
   */
  it('folds the script list to a strip that names the open script', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:list_scripts') {
        return [{ id: 'fresh', name: 'Fresh', filename: 'fresh_script.py', size_bytes: 8, modified_at: 1 }];
      }
      if (method === 'terminal:read_script') return { filename: 'fresh_script.py', code: 'print(1)' };
      return [];
    }) as never);
    render(<CommandConsole />);
    fireEvent.click(await screen.findByText('Fresh'));
    await waitFor(() => expect(screen.getByLabelText('Script name')).toHaveValue('fresh_script.py'));

    fireEvent.click(screen.getByTitle('Hide script list'));
    const strip = await screen.findByRole('button', { name: 'Scripts, fresh_script.py' });
    expect(screen.queryByText('Fresh')).not.toBeInTheDocument();

    fireEvent.click(strip);
    expect(await screen.findByText('Fresh')).toBeInTheDocument();
  });

  /**
   * The names the console has ready at the start come from the backend and show as the first
   * Workspace rows.
   */
  it('lists the preloaded names from the backend in the Workspace table', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:get_preloaded_objects') {
        return [{ name: 'astrometrics', type: 'module', description: 'The processing library', available: true }];
      }
      return [];
    }) as never);
    render(<CommandConsole />);
    expect(await screen.findByText('The processing library')).toBeInTheDocument();
  });

  /**
   * Notebooks are grouped under a heading per library, notebooks in their numbered order first,
   * then scripts.
   */
  it('groups the notebooks by library in numbered order', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:list_recipes') {
        return [
          { id: 'w_script', name: 'Plan Helper', filename: 'plan_helper.py', category: 'wayfinding', kind: 'script', description: '', size_bytes: 1 },
          { id: 'a10', name: '10. Stacking', filename: '10_stacking.ipynb', category: 'astrometrics', kind: 'notebook', description: '', size_bytes: 1 },
          { id: 'a2', name: '2. Inspecting FITS', filename: '2_inspecting.ipynb', category: 'astrometrics', kind: 'notebook', description: '', size_bytes: 1 },
          { id: 'a_script', name: 'Quick Look', filename: 'quick_look.py', category: 'astrometrics', kind: 'script', description: '', size_bytes: 1 },
        ];
      }
      return [];
    }) as never);
    render(<CommandConsole />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Examples (4)' }));
    const names = ['Astrometrics', '2. Inspecting FITS', '10. Stacking', 'Quick Look', 'Wayfinding', 'Plan Helper']
      .map((text) => screen.getByText(text));
    for (let index = 1; index < names.length; index += 1) {
      expect(names[index - 1].compareDocumentPosition(names[index]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  /**
   * The Workspace folds down to its title bar and opens again; the title button says whether
   * it is open.
   */
  it('folds the Workspace to its title bar', async () => {
    vi.spyOn(backendApi, 'callBackend').mockResolvedValue([] as never);
    render(<CommandConsole />);
    const hide = await screen.findByTitle('Hide the Workspace');
    expect(hide).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(hide);
    const show = await screen.findByTitle('Show the Workspace');
    expect(show).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('columnheader', { name: 'Name' })).not.toBeInTheDocument();
    fireEvent.click(show);
    expect(await screen.findByRole('columnheader', { name: 'Name' })).toBeInTheDocument();
  });

  /**
   * The Console panel folds to a strip on the right edge that names the open tab, and opens
   * again from the strip.
   */
  it('folds the Console panel to a right-edge strip', async () => {
    vi.spyOn(backendApi, 'callBackend').mockResolvedValue([] as never);
    render(<CommandConsole />);
    const hide = await screen.findByTitle('Hide the Console');
    expect(hide).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(hide);
    const strip = await screen.findByRole('button', { name: 'Console, Terminal' });
    expect(document.querySelector('.collapsible-column--collapsed-right')).not.toBeNull();
    expect(screen.queryByPlaceholderText(/Type Python expression or command/i)).not.toBeInTheDocument();
    fireEvent.click(strip);
    expect(await screen.findByPlaceholderText(/Type Python expression or command/i)).toBeInTheDocument();
  });

  /**
   * The Editor toolbar holds only the name, Run, Save and New, so it fits on one row; the
   * Workspace header has only its title, and the terminal has no Clear button.
   */
  it('keeps the toolbar to the script controls and drops the old buttons', async () => {
    vi.spyOn(backendApi, 'callBackend').mockResolvedValue([] as never);
    render(<CommandConsole />);
    const toolbar = await screen.findByRole('toolbar', { name: 'Script' });
    const buttons = Array.from(toolbar.querySelectorAll('button')).map((button) => button.textContent);
    expect(buttons).toEqual(['Run', 'Save', 'New']);
    expect(screen.queryByRole('button', { name: 'Reset Workspace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear Terminal' })).not.toBeInTheDocument();
  });

  /**
   * While code runs, the terminal names it with its elapsed time and the Ctrl+C hint, Run is
   * off, and Ctrl+C in the terminal asks the backend to stop it. The stopped run's reply shows
   * "Stopped." and the Workspace reads its variables again.
   */
  it('shows the running line and stops the run on Ctrl+C', async () => {
    let finishRun: (value: unknown) => void = () => undefined;
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:execute') {
        return new Promise((resolve) => {
          finishRun = resolve;
        });
      }
      if (method === 'terminal:stop') return { stopped: ['command'] };
      return [];
    }) as never);
    render(<CommandConsole />);

    const input = screen.getByPlaceholderText(/Type Python expression or command/i);
    fireEvent.change(input, { target: { value: 'slow()' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Running command');
    expect(status).toHaveTextContent('0:00');
    expect(status).toHaveTextContent('Ctrl+C to stop');
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();

    // A second line waits in the box while the first runs.
    fireEvent.change(input, { target: { value: 'other()' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(callSpy.mock.calls.filter(([method]) => method === 'terminal:execute')).toHaveLength(1);

    fireEvent.keyDown(input, { key: 'c', ctrlKey: true });
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:stop', {}));

    const workspaceReads = callSpy.mock.calls.filter(([method]) => method === 'terminal:get_workspace').length;
    await act(async () => {
      finishRun({ status: 'stopped', stdout: 'Stopped.\n', stderr: '', plots: [], interactive_plots: [], execution_time_ms: 5, workspace: [] });
    });
    expect(await screen.findByText('Stopped.')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).not.toBeDisabled();
    expect(callSpy.mock.calls.filter(([method]) => method === 'terminal:get_workspace').length).toBeGreaterThan(workspaceReads);
  });

  /**
   * A script run from the editor shows its own name on the running line.
   */
  it('names the running script on the running line', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:execute') return new Promise(() => undefined);
      return [];
    }) as never);
    render(<CommandConsole />);
    fireEvent.change(screen.getByLabelText('Script name'), { target: { value: 'survey' } });
    fireEvent.click(screen.getByTestId('editor-run-btn'));
    expect(await screen.findByRole('status')).toHaveTextContent('Running survey.py');
  });

  /**
   * `clear` clears the screen, `clear('workspace')` clears the variables, another argument prints
   * how to use it, and a line that only starts with "clear" goes to Python.
   */
  it('handles clear and clear(\'workspace\') in the terminal', async () => {
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:execute') {
        return { status: 'success', stdout: 'out-line', stderr: '', plots: [], interactive_plots: [], execution_time_ms: 1, workspace: [] };
      }
      return [];
    }) as never);
    render(<CommandConsole />);
    const input = screen.getByPlaceholderText(/Type Python expression or command/i);
    const typeLine = (line: string) => {
      fireEvent.change(input, { target: { value: line } });
      fireEvent.keyDown(input, { key: 'Enter' });
    };

    typeLine('clear = 5');
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:execute', { code_str: 'clear = 5', source: 'repl' }, { timeoutMs: 0 }));
    expect(await screen.findByText('out-line')).toBeInTheDocument();

    typeLine('clear()');
    await waitFor(() => expect(screen.queryByText('out-line')).not.toBeInTheDocument());
    expect(screen.getByText(/type help for the preloaded names/)).toBeInTheDocument();

    typeLine('clear("workspace")');
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:reset_workspace', {}));
    expect(await screen.findByText(/Your variables were cleared/)).toBeInTheDocument();

    typeLine('clear(1)');
    expect(await screen.findByText(/Type clear to clear the screen/)).toBeInTheDocument();
    expect(callSpy.mock.calls.filter(([method]) => method === 'terminal:execute')).toHaveLength(1);
  });

  /**
   * `doc name` is sent as `doc("name")`; a reply that asks to open a reference entry switches the
   * Console to the Documentation tab.
   */
  it('sends doc name to Python and opens the Documentation tab for its entry', async () => {
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:execute') {
        return {
          status: 'success', stdout: 'Opening TargetCatalog.delete_images in the Documentation tab.\n', stderr: '',
          plots: [], interactive_plots: [], execution_time_ms: 1, workspace: [],
          app_requests: [{ kind: 'doc', topic_id: 'api/astrometricslib', anchor_id: 'api-TargetCatalog-delete_images', qualified_name: 'TargetCatalog.delete_images' }],
        };
      }
      return [];
    }) as never);
    render(<CommandConsole />);
    const input = screen.getByPlaceholderText(/Type Python expression or command/i);
    fireEvent.change(input, { target: { value: 'doc delete_images' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:execute', { code_str: 'doc("delete_images")', source: 'repl' }, { timeoutMs: 0 }));
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Documentation' })).toHaveAttribute('aria-selected', 'true'));
  });

  /**
   * A reply with a show request switches this window to that display when no other window shows it.
   */
  it('opens what show() asked for through the app navigation', async () => {
    vi.spyOn(backendApi, 'callBackend').mockImplementation((async (method: string) => {
      if (method === 'terminal:execute') {
        return {
          status: 'success', stdout: '', stderr: '', plots: [], interactive_plots: [], execution_time_ms: 1, workspace: [],
          app_requests: [{ kind: 'show', display: 'Image Processing', target: 'M 31', star_id: null, star_name: null, path: null }],
        };
      }
      return [];
    }) as never);
    const modes: string[] = [];
    const onModeChange = (event: Event) => modes.push((event as CustomEvent<string>).detail);
    window.addEventListener('astrometrics:modeChange', onModeChange);
    try {
      render(<CommandConsole />);
      const input = screen.getByPlaceholderText(/Type Python expression or command/i);
      fireEvent.change(input, { target: { value: 'show(target="M 31")' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() => expect(modes).toContain('Image Processing'));
    } finally {
      window.removeEventListener('astrometrics:modeChange', onModeChange);
    }
  });

  /**
   * Code an AI agent ran does not answer this window, so its hand-off makes the Workspace read
   * its variables again.
   */
  it('reads the Workspace again after an AI agent ran code', async () => {
    const callSpy = vi.spyOn(backendApi, 'callBackend').mockResolvedValue([] as never);
    render(<CommandConsole />);
    await waitFor(() => expect(callSpy).toHaveBeenCalledWith('terminal:get_workspace', {}));
    const before = callSpy.mock.calls.filter(([method]) => method === 'terminal:get_workspace').length;
    act(() => {
      postHandoff('Command Console', COMMAND_CONSOLE_EVENT_KIND, { action: 'terminal:agent_exec', payload: { code: 'x = 1' } }, { queue: true });
    });
    await waitFor(() =>
      expect(callSpy.mock.calls.filter(([method]) => method === 'terminal:get_workspace').length).toBeGreaterThan(before)
    );
  });
});
