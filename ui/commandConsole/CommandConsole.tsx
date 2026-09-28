/**
 * @file CommandConsole.tsx
 * @description Central 3-column scientific workbench integrating script recipes, user scripts,
 * workspace variables, CodeMirror editor, Python REPL, and documentation viewer.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Panel, Group, Separator } from 'react-resizable-panels';
import { callBackend } from '../common/services/backendApi';
import { ConsoleSidebar, ScriptRecipeItem, UserScriptItem } from './ConsoleSidebar';
import { WorkspaceTable, WorkspaceVariable } from './WorkspaceTable';
import { CodeEditor } from './CodeEditor';
import { TerminalPane, TerminalEntry } from './TerminalPane';
import { DocViewer } from './DocViewer';
import './commandConsole.css';

export const CommandConsole: React.FC = () => {
  // Left Column State
  const [recipes, setRecipes] = useState<ScriptRecipeItem[]>([]);
  const [userScripts, setUserScripts] = useState<UserScriptItem[]>([]);
  const [workspaceVariables, setWorkspaceVariables] = useState<WorkspaceVariable[]>([]);
  const [activeSidebarTab, setActiveSidebarTab] = useState<'scripts' | 'docs'>('scripts');

  // Center Column State (Editor)
  const [editorCode, setEditorCode] = useState<string>('# Astrometrics Scripting Workbench\nprint("Welcome to Astrometrics Command Console")\n');
  const [activeFilename, setActiveFilename] = useState<string>('scratch.py');
  const [isExecutingScript, setIsExecutingScript] = useState<boolean>(false);

  // Right Column State (Tabs)
  const [rightTab, setRightTab] = useState<'terminal' | 'docs'>('terminal');
  const [terminalEntries, setTerminalEntries] = useState<TerminalEntry[]>([]);
  const [isExecutingTerminal, setIsExecutingTerminal] = useState<boolean>(false);

  // Documentation State
  const [docTopics, setDocTopics] = useState<Array<{ id: string; title: string; path: string; category: string }>>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [docContent, setDocContent] = useState<string>('');
  const [docLoading, setDocLoading] = useState<boolean>(false);

  // Initial Data Fetch
  const loadSidebarData = useCallback(async () => {
    try {
      const [recipeList, scriptList, wsList] = await Promise.all([
        callBackend('terminal:list_recipes', {}).catch(() => []),
        callBackend('terminal:list_scripts', {}).catch(() => []),
        callBackend('terminal:get_workspace', {}).catch(() => []),
      ]);
      setRecipes(recipeList || []);
      setUserScripts(scriptList || []);
      setWorkspaceVariables(wsList || []);
    } catch (err) {
      console.warn('Failed loading Command Console data:', err);
    }
  }, []);

  useEffect(() => {
    loadSidebarData();
  }, [loadSidebarData]);

  // Load documentation topics list
  useEffect(() => {
    callBackend('docs:list_topics', {})
      .then((topics) => {
        setDocTopics(topics || []);
        if (topics && topics.length > 0 && !selectedDocId) {
          handleSelectDoc(topics[0].id);
        }
      })
      .catch((err) => console.warn('Failed loading doc topics:', err));
  }, []);

  // Listen to WebSocket events (agent executions, plot generations, editor sync)
  useEffect(() => {
    const handleRemoteAction = (event: CustomEvent) => {
      const { type, action, payload } = event.detail || {};
      if (type === 'UI_EVENT') {
        if (action === 'terminal:agent_exec') {
          setTerminalEntries((prev) => [
            ...prev,
            {
              id: String(Date.now()),
              command: payload?.code || '',
              source: 'agent',
            },
          ]);
        } else if (action === 'terminal:plot_generated') {
          if (window.astrometrics?.dialog?.openFigureWindow) {
            if (payload?.interactive_url) {
              window.astrometrics.dialog.openFigureWindow({
                plotPath: payload.plot_path,
                interactiveUrl: payload.interactive_url,
              });
            } else if (payload?.plot_path) {
              window.astrometrics.dialog.openFigureWindow(payload.plot_path);
            }
          }
        } else if (action === 'editor:sync' && payload?.code !== undefined) {
          setEditorCode(payload.code);
        }
      }
    };

    window.addEventListener('astrometrics:uiEvent' as any, handleRemoteAction as any);
    return () => {
      window.removeEventListener('astrometrics:uiEvent' as any, handleRemoteAction as any);
    };
  }, []);

  // Handle Documentation Selection
  const handleSelectDoc = async (topicId: string) => {
    setSelectedDocId(topicId);
    setDocLoading(true);
    try {
      const topicData = await callBackend('docs:get_topic', { topic_id: topicId });
      setDocContent(topicData.content);
    } catch (err) {
      setDocContent(`Error loading documentation topic: ${String(err)}`);
    } finally {
      setDocLoading(false);
    }
  };

  // Run Script from Editor
  const handleRunEditor = async () => {
    if (!editorCode.trim() || isExecutingScript) return;
    setIsExecutingScript(true);

    try {
      const response = await callBackend('terminal:execute', {
        code_str: editorCode,
        source: 'editor',
      });

      // Update terminal with execution record
      setTerminalEntries((prev) => [
        ...prev,
        {
          id: String(Date.now()),
          command: `# Ran ${activeFilename}`,
          stdout: response.stdout,
          stderr: response.stderr,
          executionTimeMs: response.execution_time_ms,
        },
      ]);

      if (response.workspace) {
        setWorkspaceVariables(response.workspace);
      }

      // Automatically open pop-up windows for generated plots
      if (response.interactive_plots && response.interactive_plots.length > 0) {
        for (const item of response.interactive_plots) {
          if (window.astrometrics?.dialog?.openFigureWindow) {
            window.astrometrics.dialog.openFigureWindow({
              plotPath: item.plot_path,
              interactiveUrl: item.url,
            });
          }
        }
      } else if (response.plots && response.plots.length > 0) {
        for (const plotPath of response.plots) {
          if (window.astrometrics?.dialog?.openFigureWindow) {
            window.astrometrics.dialog.openFigureWindow(plotPath);
          }
        }
      }
    } catch (err: any) {
      setTerminalEntries((prev) => [
        ...prev,
        {
          id: String(Date.now()),
          command: `# Ran ${activeFilename}`,
          stderr: String(err?.message || err),
        },
      ]);
    } finally {
      setIsExecutingScript(false);
    }
  };

  // Execute single REPL command
  const handleExecuteRepl = async (command: string) => {
    if (!command.trim() || isExecutingTerminal) return;
    setIsExecutingTerminal(true);

    try {
      const response = await callBackend('terminal:execute', {
        code_str: command,
        source: 'repl',
      });

      setTerminalEntries((prev) => [
        ...prev,
        {
          id: String(Date.now()),
          command,
          stdout: response.stdout,
          stderr: response.stderr,
          executionTimeMs: response.execution_time_ms,
        },
      ]);

      if (response.workspace) {
        setWorkspaceVariables(response.workspace);
      }

      if (response.interactive_plots && response.interactive_plots.length > 0) {
        for (const item of response.interactive_plots) {
          if (window.astrometrics?.dialog?.openFigureWindow) {
            window.astrometrics.dialog.openFigureWindow({
              plotPath: item.plot_path,
              interactiveUrl: item.url,
            });
          }
        }
      } else if (response.plots && response.plots.length > 0) {
        for (const plotPath of response.plots) {
          if (window.astrometrics?.dialog?.openFigureWindow) {
            window.astrometrics.dialog.openFigureWindow(plotPath);
          }
        }
      }
    } catch (err: any) {
      setTerminalEntries((prev) => [
        ...prev,
        {
          id: String(Date.now()),
          command,
          stderr: String(err?.message || err),
        },
      ]);
    } finally {
      setIsExecutingTerminal(false);
    }
  };

  // Clear Terminal History (frontend only, workspace variables are untouched)
  const handleClearTerminal = () => {
    setTerminalEntries([]);
  };

  // Reset Workspace (discards console-local variables, keeps terminal history)
  const handleResetWorkspace = async () => {
    try {
      const ws = await callBackend('terminal:reset_workspace', {});
      setWorkspaceVariables(ws || []);
      setTerminalEntries((prev) => [
        ...prev,
        {
          id: String(Date.now()),
          command: '# Workspace reset',
          stdout: 'All console-local variables were cleared; built-in objects were restored.',
        },
      ]);
    } catch (err) {
      alert(`Failed to reset workspace: ${String(err)}`);
    }
  };

  // Save User Script
  const handleSaveUserScript = async () => {
    let filename = activeFilename;
    if (filename === 'scratch.py' || filename.startsWith('recipe_')) {
      const chosen = prompt('Enter script filename to save (e.g. my_pipeline.py):', 'my_script.py');
      if (!chosen) return;
      filename = chosen;
      setActiveFilename(filename);
    }

    try {
      await callBackend('terminal:save_script', {
        filename,
        content: editorCode,
      });
      const updatedList = await callBackend('terminal:list_scripts', {});
      setUserScripts(updatedList || []);
    } catch (err) {
      alert(`Failed to save script: ${String(err)}`);
    }
  };

  // Load Recipe into Editor
  const handleSelectRecipe = async (recipe: ScriptRecipeItem) => {
    try {
      const recipeData = await callBackend('terminal:get_recipe', { recipe_id: recipe.id });
      setEditorCode(recipeData.code);
      setActiveFilename(`recipe_${recipe.filename}`);
    } catch (err) {
      console.warn('Failed to load recipe:', err);
    }
  };

  // Load User Script into Editor
  const handleSelectUserScript = async (script: UserScriptItem) => {
    try {
      const data = await callBackend('terminal:read_script', { filename: script.filename });
      setEditorCode(data.code);
      setActiveFilename(script.filename);
    } catch (err) {
      console.warn('Failed reading user script:', err);
    }
  };

  return (
    <div className="command-console">
      <div className="command-console__container">
        <Group orientation="horizontal">
          {/* Column 1: Sidebar & Workspace Table (Split Vertically) */}
          <Panel defaultSize={25} minSize={18}>
            <Group orientation="vertical">
              <Panel defaultSize={55} minSize={30}>
                <ConsoleSidebar
                  recipes={recipes}
                  userScripts={userScripts}
                  docTopics={docTopics}
                  selectedDocId={selectedDocId}
                  activeSidebarTab={activeSidebarTab}
                  onTabChange={setActiveSidebarTab}
                  onSelectRecipe={handleSelectRecipe}
                  onSelectUserScript={handleSelectUserScript}
                  onSelectDocTopic={(topicId) => {
                    handleSelectDoc(topicId);
                    setRightTab('docs');
                  }}
                  onNewScript={() => {
                    setEditorCode('# New Python Script\n');
                    setActiveFilename('scratch.py');
                  }}
                />
              </Panel>
              <Separator className="console-resize-handle console-resize-handle--horizontal" />
              <Panel defaultSize={45} minSize={25}>
                <WorkspaceTable
                  variables={workspaceVariables}
                  onRefresh={async () => {
                    const ws = await callBackend('terminal:get_workspace', {});
                    setWorkspaceVariables(ws || []);
                  }}
                />
              </Panel>
            </Group>
          </Panel>

          <Separator className="console-resize-handle console-resize-handle--vertical" />

          {/* Column 2: Code Editor (Center) */}
          <Panel defaultSize={40} minSize={25}>
            <CodeEditor
              value={editorCode}
              onChange={setEditorCode}
              onRun={handleRunEditor}
              onSave={handleSaveUserScript}
              onClear={() => setEditorCode('')}
              filename={activeFilename}
              isRunning={isExecutingScript}
            />
          </Panel>

          <Separator className="console-resize-handle console-resize-handle--vertical" />

          {/* Column 3: Tabbed Panel (Terminal, Documentation) */}
          <Panel defaultSize={35} minSize={25}>
            <div className="console-panel">
              <div className="console-panel__header" style={{ padding: 0 }}>
                <div style={{ display: 'flex', width: '100%', height: '100%' }}>
                  <button
                    className={`console-sidebar__tab ${rightTab === 'terminal' ? 'console-sidebar__tab--active' : ''}`}
                    onClick={() => setRightTab('terminal')}
                    type="button"
                  >
                    Terminal
                  </button>
                  <button
                    className={`console-sidebar__tab ${rightTab === 'docs' ? 'console-sidebar__tab--active' : ''}`}
                    onClick={() => {
                      setRightTab('docs');
                      setActiveSidebarTab('docs');
                    }}
                    type="button"
                  >
                    Documentation
                  </button>
                </div>
              </div>

              <div className="console-panel__body">
                {rightTab === 'terminal' && (
                  <TerminalPane
                    entries={terminalEntries}
                    onExecute={handleExecuteRepl}
                    onClear={handleClearTerminal}
                    onReset={handleResetWorkspace}
                    isRunning={isExecutingTerminal}
                  />
                )}
                {rightTab === 'docs' && (
                  <DocViewer
                    topics={docTopics}
                    selectedTopicId={selectedDocId}
                    content={docContent}
                    loading={docLoading}
                    onSelectTopic={handleSelectDoc}
                  />
                )}
              </div>
            </div>
          </Panel>
        </Group>
      </div>
    </div>
  );
};
