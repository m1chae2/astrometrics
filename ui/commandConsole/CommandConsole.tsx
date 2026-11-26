/**
 * @file CommandConsole.tsx
 * @description The Command Console display, a place to try the public Python API by hand or
 * with an AI. The left column lists your saved scripts and the examples on two tabs; it folds to
 * a thin strip like the other displays' left columns. The Workspace folds down to its title bar.
 * The center holds the code editor with a one-row toolbar (script name, Run, Save, New), the
 * Workspace table, and a Terminal / Documentation pair of tabs. Figures open in their own
 * windows. One run goes at a time: the terminal names it and its elapsed time, and Ctrl+C in the
 * terminal stops it. The terminal handles `clear`, `clear('workspace')` and `doc name` itself.
 * Code that calls `show(...)` opens the target, star or file in the app; another window that
 * shows that display handles it, otherwise this window switches to it.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Panel, Group, Separator } from 'react-resizable-panels';
import { callBackend, type ConsoleAppRequest } from '../common/services/backendApi';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { SectionPanel } from '../common/components/SectionPanel';
import { Tabs } from '../common/components/Tabs';
import { Button } from '../common/components/Button';
import { TextInput } from '../common/components/TextInput';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { CollapsibleColumn, useCollapsibleColumn } from '../common/components/CollapsibleColumn';
import { Toolbar, ToolbarGroup } from '../common/components/Toolbar';
import { ScriptList, recipeListId, userScriptListId, type RecipeItem, type UserScriptItem } from './ScriptList';
import { WorkspaceTable, type PreloadedObject, type WorkspaceVariable } from './WorkspaceTable';
import { CodeEditor } from './CodeEditor';
import { TerminalPane, type RunningCode, type TerminalEntry } from './TerminalPane';
import { CLEAR_USAGE, parseTerminalCommand, showRequestIntent } from './terminalCommands';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { DocumentationPane, type DocFocusEntry } from './DocumentationPane';
import type { DocTopicItem } from './DocViewer';
import { usePersistentFlag } from '../common/hooks/usePersistentFlag';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import { useHandoffConsumer } from '../common/utils/displayHandoffStore';
import { COMMAND_CONSOLE_EVENT_KIND, type CommandConsoleEvent } from '../common/utils/commandConsoleEvents';
import './commandConsole.css';

/** The code in the editor when the console opens. */
const WELCOME_CODE = '# Write Python here, or open a script or notebook from the list.\n# Press Ctrl+Enter to run it.\n';

/** The right-hand tabs. */
type ConsoleTab = 'terminal' | 'docs';

/** The label the running line shows for a line typed at the terminal. */
const TYPED_LINE_LABEL = 'command';

/** One interactive plot, as the backend reports it after running code. */
interface InteractivePlot {
  plot_path?: string;
  url?: string;
  interactive_url?: string;
  width?: number;
  height?: number;
}

/**
 * Opens the app's figure windows for plots the backend reports.
 *
 * Each interactive plot opens its own page, sized to the figure. Plain PNG
 * pictures open only when there is no interactive plot.
 *
 * @param interactivePlots - Plots with an interactive page, if any.
 * @param plotPaths - PNG pictures of the plots, if any.
 */
function openFigureWindows(interactivePlots: InteractivePlot[] = [], plotPaths: string[] = []): void {
  const openFigureWindow = window.astrometrics?.dialog?.openFigureWindow;
  if (!openFigureWindow) return;
  if (interactivePlots.length > 0) {
    for (const plot of interactivePlots) {
      openFigureWindow({
        plotPath: plot.plot_path,
        interactiveUrl: plot.url ?? plot.interactive_url,
        width: plot.width,
        height: plot.height,
      });
    }
    return;
  }
  for (const plotPath of plotPaths) {
    openFigureWindow(plotPath);
  }
}

/**
 * Turns the name typed in the toolbar into a script file name: only the last part of a path
 * is kept, and ".py" is added when it is missing. The backend does the same.
 *
 * @param typedName The name typed in the toolbar, such as "my analysis".
 * @returns The file name, such as "my analysis.py", or an empty string when nothing was typed.
 */
export function scriptFilenameFromName(typedName: string): string {
  const lastPart = typedName.trim().split(/[\\/]/).pop() ?? '';
  if (!lastPart) return '';
  return lastPart.endsWith('.py') ? lastPart : `${lastPart}.py`;
}

/**
 * Gives the name a recipe gets when you save it as your own script: its file name with ".py",
 * so a notebook "light_curve.ipynb" becomes "light_curve.py".
 *
 * @param recipeFilename The recipe's file name.
 * @returns The suggested script file name.
 */
export function scriptNameForRecipe(recipeFilename: string): string {
  return `${recipeFilename.replace(/\.(ipynb|py)$/i, '')}.py`;
}

/**
 * Builds the text of an error for the terminal.
 *
 * @param error What was thrown.
 * @returns The message.
 */
function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Shows the Command Console display.
 *
 * @returns The display.
 */
export const CommandConsole: React.FC = () => {
  // Script list
  const [recipes, setRecipes] = useState<RecipeItem[]>([]);
  const [userScripts, setUserScripts] = useState<UserScriptItem[]>([]);
  const [isLoadingLists, setIsLoadingLists] = useState(true);
  const [listsFailed, setListsFailed] = useState(false);

  // Editor
  const [editorCode, setEditorCode] = useState<string>(WELCOME_CODE);
  /** The name typed in the toolbar; Save writes the script under it. Empty for a new script. */
  const [scriptName, setScriptName] = useState('');
  /** The list id of the script or recipe open in the editor, if it came from the list. */
  const [openItemId, setOpenItemId] = useState<string | undefined>(undefined);
  /** The code running now (a script or a typed line), or null. One run goes at a time. */
  const [running, setRunning] = useState<RunningCode | null>(null);
  /** The file name waiting for you to confirm that it may be replaced. */
  const [overwriteFilename, setOverwriteFilename] = useState<string | null>(null);

  // Workspace and terminal
  const [workspaceVariables, setWorkspaceVariables] = useState<WorkspaceVariable[]>([]);
  const [preloadedObjects, setPreloadedObjects] = useState<PreloadedObject[]>([]);
  const [activeTab, setActiveTab] = useState<ConsoleTab>('terminal');
  const [terminalEntries, setTerminalEntries] = useState<TerminalEntry[]>([]);
  // The Workspace folds down to its title bar, giving the editor the height; remembered for this viewer.
  const [workspaceCollapsed, setWorkspaceCollapsed] = usePersistentFlag('astrometrics:commandConsole:workspaceCollapsed', false);
  // The Console panel (Terminal and Documentation) folds to a strip on the right edge, giving the
  // editor the width; remembered for this viewer.
  const [consoleCollapsed, setConsoleCollapsed] = useCollapsibleColumn('astrometrics:commandConsole:consoleCollapsed');

  // Documentation
  const [docTopics, setDocTopics] = useState<DocTopicItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [docContent, setDocContent] = useState('');
  const [docLoading, setDocLoading] = useState(false);
  const [docFocusEntry, setDocFocusEntry] = useState<DocFocusEntry | undefined>(undefined);

  /** Adds one entry to the terminal history. */
  const addTerminalEntry = useCallback((entry: Omit<TerminalEntry, 'id'>) => {
    setTerminalEntries((previous) => [...previous, { id: `${Date.now()}-${previous.length}`, ...entry }]);
  }, []);

  /** Reads your saved scripts and the recipes. */
  const loadScriptLists = useCallback(async () => {
    try {
      const [recipeList, scriptList] = await Promise.all([
        callBackend('terminal:list_recipes', {}),
        callBackend('terminal:list_scripts', {}),
      ]);
      setRecipes(recipeList || []);
      setUserScripts(scriptList || []);
      setListsFailed(false);
    } catch {
      setListsFailed(true);
    } finally {
      setIsLoadingLists(false);
    }
  }, []);
  useReportModeReady('Command Console', !isLoadingLists);

  /** Reads only your saved scripts again, after one is saved here or elsewhere. */
  const reloadUserScripts = useCallback(async () => {
    try {
      const scriptList = await callBackend('terminal:list_scripts', {});
      setUserScripts(scriptList || []);
    } catch {
      // The list keeps what it showed; the next save or reload tries again.
    }
  }, []);

  /** Reads the Workspace variables again. */
  const refreshWorkspace = useCallback(async () => {
    try {
      const variables = await callBackend('terminal:get_workspace', {});
      setWorkspaceVariables(variables || []);
    } catch {
      // The table keeps what it showed.
    }
  }, []);

  /** Shows one documentation topic. */
  const selectDocTopic = useCallback(async (topicId: string) => {
    setSelectedDocId(topicId);
    setDocLoading(true);
    try {
      const topic = await callBackend('docs:get_topic', { topic_id: topicId });
      setDocContent(topic.content);
    } catch (error) {
      setDocContent(`Could not read this topic: ${errorText(error)}`);
    } finally {
      setDocLoading(false);
    }
  }, []);

  useEffect(() => {
    loadScriptLists();
    refreshWorkspace();
    callBackend('terminal:get_preloaded_objects', {})
      .then((objects) => setPreloadedObjects(objects || []))
      .catch(() => setPreloadedObjects([]));
    callBackend('docs:list_topics', {})
      .then((topics) => {
        setDocTopics(topics || []);
        if (topics && topics.length > 0) selectDocTopic(topics[0].id);
      })
      .catch(() => setDocTopics([]));
  }, [loadScriptLists, refreshWorkspace, selectDocTopic]);

  // Shows what an AI agent ran, plots it made, editor syncs, and script saves. They arrive
  // through the hand-off store, which keeps them in order while this display is
  // hidden or paused, so none is lost.
  useHandoffConsumer<CommandConsoleEvent>('Command Console', COMMAND_CONSOLE_EVENT_KIND, ({ action, payload }) => {
    if (action === 'terminal:agent_exec') {
      addTerminalEntry({ command: payload?.code || '', source: 'agent' });
      // An agent's run does not answer this window, so the table reads the variables itself.
      refreshWorkspace();
    } else if (action === 'terminal:plot_generated') {
      if (payload?.interactive_url) {
        openFigureWindows([payload]);
      } else if (payload?.plot_path) {
        openFigureWindows([], [payload.plot_path]);
      }
    } else if (action === 'editor:sync' && payload?.code !== undefined) {
      setEditorCode(payload.code);
    } else if (action === 'scripts:changed') {
      // A script was saved by an AI agent or another window: reload the list.
      reloadUserScripts();
    }
  });

  /**
   * Carries out what the code asked the app to show: `show(...)` opens a target, star or file
   * (in another window that shows that display, or else here), and `doc(...)` opens a
   * reference entry in the Documentation tab.
   *
   * @param requests The requests the run returned.
   */
  const handleAppRequests = (requests: ConsoleAppRequest[] = []) => {
    for (const request of requests) {
      if (request.kind === 'show') {
        void navigateToElement(showRequestIntent(request));
      } else if (request.kind === 'doc') {
        setConsoleCollapsed(false);
        setActiveTab('docs');
        setDocFocusEntry({ topicId: request.topic_id, anchorId: request.anchor_id, nonce: Date.now() });
      }
    }
  };

  /**
   * Runs code in the console and shows the output in the terminal. A run has no time limit:
   * Ctrl+C in the terminal stops it.
   *
   * @param code The code to run.
   * @param source "editor" for the whole script, "repl" for one terminal line.
   * @param command The line shown after the prompt in the terminal history.
   * @param label The name the run history gives the run, if any.
   */
  const runCode = async (code: string, source: 'editor' | 'repl', command: string, label?: string) => {
    try {
      const response = await callBackend('terminal:execute', {
        code_str: code,
        source,
        ...(label ? { label } : {}),
      }, { timeoutMs: 0 });
      addTerminalEntry({
        command,
        stdout: response.stdout,
        stderr: response.stderr,
        executionTimeMs: response.execution_time_ms,
      });
      if (response.workspace) setWorkspaceVariables(response.workspace);
      if (response.status === 'stopped') refreshWorkspace();
      openFigureWindows(response.interactive_plots, response.plots);
      handleAppRequests(response.app_requests);
    } catch (error) {
      addTerminalEntry({ command, stderr: errorText(error) });
    }
  };

  /** Stops the running code (Ctrl+C in the terminal). The run's reply then says "Stopped.". */
  const handleStop = async () => {
    try {
      await callBackend('terminal:stop', {});
    } catch (error) {
      addTerminalEntry({ command: '# Stop', stderr: `Could not stop the code: ${errorText(error)}` });
    }
  };

  const openFilename = scriptFilenameFromName(scriptName);

  /** Runs the whole script in the editor. */
  const handleRunScript = async () => {
    if (!editorCode.trim() || running) return;
    const scriptLabel = openFilename || 'the new script';
    setRunning({ label: scriptLabel, startedAt: Date.now() });
    setActiveTab('terminal');
    // A script with a name gives its name to the run history. A new script has none,
    // so the backend uses its first line.
    await runCode(editorCode, 'editor', `# Ran ${scriptLabel}`, openFilename || undefined);
    setRunning(null);
  };

  /**
   * Clears the variables your code made and puts back the preloaded names (`clear('workspace')`).
   *
   * @param command The line typed, shown in the history.
   */
  const clearWorkspace = async (command: string) => {
    try {
      const variables = await callBackend('terminal:reset_workspace', {});
      setWorkspaceVariables(variables || []);
      addTerminalEntry({ command, stdout: 'Your variables were cleared; the preloaded names stay.' });
    } catch (error) {
      addTerminalEntry({ command, stderr: `Could not clear the workspace: ${errorText(error)}` });
    }
    refreshWorkspace();
  };

  /**
   * Runs one line typed in the terminal. `clear`, `clear('workspace')` and `doc name` are
   * handled here; every other line goes to Python.
   *
   * @param line The line typed.
   */
  const handleRunLine = async (line: string) => {
    if (!line.trim() || running) return;
    const command = parseTerminalCommand(line);
    if (command.kind === 'clear-terminal') {
      setTerminalEntries([]);
      return;
    }
    if (command.kind === 'clear-workspace') {
      await clearWorkspace(line);
      return;
    }
    if (command.kind === 'clear-usage') {
      addTerminalEntry({ command: line, stdout: CLEAR_USAGE });
      return;
    }
    setRunning({ label: TYPED_LINE_LABEL, startedAt: Date.now() });
    await runCode(command.code, 'repl', line);
    setRunning(null);
  };

  /**
   * Writes the editor's code to one of your scripts and opens that script in the list.
   *
   * @param filename The file name to write.
   */
  const writeScript = async (filename: string) => {
    try {
      const saved = await callBackend('terminal:save_script', { filename, content: editorCode });
      setScriptName(saved.filename);
      setOpenItemId(userScriptListId(saved.filename));
      await reloadUserScripts();
    } catch (error) {
      addTerminalEntry({ command: `# Save ${filename}`, stderr: `Could not save the script: ${errorText(error)}` });
    }
  };

  /** Saves the editor's code under the name in the toolbar, asking first before replacing another script. */
  const handleSave = () => {
    if (!openFilename) return;
    const replacesOtherScript = userScripts.some((script) => script.filename === openFilename)
      && openItemId !== userScriptListId(openFilename);
    if (replacesOtherScript) {
      setOverwriteFilename(openFilename);
      return;
    }
    writeScript(openFilename);
  };

  /** Starts a new, empty script. */
  const handleNewScript = () => {
    setEditorCode('');
    setScriptName('');
    setOpenItemId(undefined);
  };

  /** Opens one of your saved scripts in the editor. */
  const handleOpenUserScript = async (script: UserScriptItem) => {
    try {
      const data = await callBackend('terminal:read_script', { filename: script.filename });
      setEditorCode(data.code);
      setScriptName(data.filename);
      setOpenItemId(userScriptListId(script.filename));
    } catch (error) {
      addTerminalEntry({ command: `# Open ${script.filename}`, stderr: `Could not open the script: ${errorText(error)}` });
    }
  };

  /** Opens a recipe in the editor. Saving it then makes it one of your own scripts. */
  const handleOpenRecipe = async (recipe: RecipeItem) => {
    try {
      const data = await callBackend('terminal:get_recipe', { recipe_id: recipe.id });
      setEditorCode(data.code);
      setScriptName(scriptNameForRecipe(data.filename || recipe.filename));
      setOpenItemId(recipeListId(recipe.id));
    } catch (error) {
      addTerminalEntry({ command: `# Open ${recipe.filename}`, stderr: `Could not open the notebook: ${errorText(error)}` });
    }
  };

  const isRecipeOpen = openItemId?.startsWith(recipeListId('')) ?? false;

  const leftPanel = (
    <ScriptList
      userScripts={userScripts}
      recipes={recipes}
      openItemId={openItemId}
      openScriptName={openFilename}
      onOpenUserScript={handleOpenUserScript}
      onOpenRecipe={handleOpenRecipe}
      isLoading={isLoadingLists}
      isError={listsFailed}
      onRetry={loadScriptLists}
    />
  );

  const scriptToolbar = (
    <Toolbar className="command-console__toolbar" ariaLabel="Script">
      <ToolbarGroup>
        <span className="command-console__name-field">
          <TextInput
            size="small"
            aria-label="Script name"
            placeholder="my_script.py"
            value={scriptName}
            onChange={(event) => setScriptName(event.target.value)}
          />
        </span>
      </ToolbarGroup>
      <ToolbarGroup>
        <Button
          variant="primary"
          size="small"
          onClick={handleRunScript}
          disabled={running !== null || !editorCode.trim()}
          title={running ? 'Code is running; Ctrl+C in the terminal stops it' : 'Run the whole script (Ctrl+Enter)'}
        >
          Run
        </Button>
        <Button
          size="small"
          onClick={handleSave}
          disabled={!openFilename}
          title={isRecipeOpen ? 'Save this example as your own script' : 'Save the script under this name'}
        >
          {isRecipeOpen ? 'Save as My Script' : 'Save'}
        </Button>
        <Button size="small" onClick={handleNewScript} title="Start a new, empty script">
          New
        </Button>
      </ToolbarGroup>
    </Toolbar>
  );

  const editorPanel = (
    <SectionPanel
      title="Editor"
      className="command-console__panel panel--no-padding"
      headerContent={scriptToolbar}
    >
      <CodeEditor value={editorCode} onChange={setEditorCode} onRun={handleRunScript} />
    </SectionPanel>
  );

  const editorArea = (
    <>
      {workspaceCollapsed ? (
        <div className="command-console__editor-stack">
          {editorPanel}
          <SectionPanel
            title="Workspace"
            collapsed="down"
            collapsedNote={`${workspaceVariables.length} variables`}
            onTitleClick={() => setWorkspaceCollapsed(false)}
            titleButtonLabel="Show the Workspace"
          >
            {null}
          </SectionPanel>
        </div>
      ) : (
        <Group orientation="vertical">
          <Panel defaultSize={65} minSize={30}>
            {editorPanel}
          </Panel>
          <Separator className="command-console__resize-handle command-console__resize-handle--horizontal" />
          <Panel defaultSize={35} minSize={15}>
            <SectionPanel
              title="Workspace"
              className="command-console__panel panel--no-padding"
              onTitleClick={() => setWorkspaceCollapsed(true)}
              titleButtonLabel="Hide the Workspace"
            >
              <WorkspaceTable variables={workspaceVariables} preloadedObjects={preloadedObjects} />
            </SectionPanel>
          </Panel>
        </Group>
      )}
    </>
  );

  const consoleTabLabel = activeTab === 'terminal' ? 'Terminal' : 'Documentation';
  const consolePanel = (
    <SectionPanel
      title="Console"
      className="command-console__panel"
      onTitleClick={() => setConsoleCollapsed(true)}
      titleButtonLabel="Hide the Console"
    >
      <div className="command-console__panel-tabs">
        <Tabs
          tabs={[
            { id: 'terminal', label: 'Terminal' },
            { id: 'docs', label: 'Documentation' },
          ]}
          selectedId={activeTab}
          onChange={(tabId) => setActiveTab(tabId as ConsoleTab)}
          ariaLabel="Console views"
          idPrefix="command-console"
          size="compact"
        />
      </div>
      {activeTab === 'terminal' ? (
        <div className="command-console__tab-body">
          <TerminalPane
            entries={terminalEntries}
            onExecute={handleRunLine}
            running={running}
            onStop={handleStop}
          />
        </div>
      ) : (
        <div className="command-console__tab-body">
          <DocumentationPane
            topics={docTopics}
            selectedTopicId={selectedDocId}
            content={docContent}
            loading={docLoading}
            onSelectTopic={selectDocTopic}
            focusEntry={docFocusEntry}
          />
        </div>
      )}
    </SectionPanel>
  );

  const centerPanel = (
    <div className="command-console__work-area">
      {consoleCollapsed ? (
        <>
          <div className="command-console__main">{editorArea}</div>
          <CollapsibleColumn
            collapsed
            side="right"
            onCollapsedChange={setConsoleCollapsed}
            stripTitle="Console"
            selectedLabel={consoleTabLabel}
            showLabel="Show the Console"
          >
            {null}
          </CollapsibleColumn>
        </>
      ) : (
        <Group orientation="horizontal">
          <Panel defaultSize={55} minSize={30}>
            {editorArea}
          </Panel>
          <Separator className="command-console__resize-handle command-console__resize-handle--vertical" />
          <Panel defaultSize={45} minSize={25}>
            {consolePanel}
          </Panel>
        </Group>
      )}
      <ConfirmDialog
        open={overwriteFilename !== null}
        title="Replace script?"
        message={`You already have a script named ${overwriteFilename ?? ''}. Replace it with the code in the editor?`}
        confirmLabel="Replace"
        onConfirm={() => {
          const filename = overwriteFilename;
          setOverwriteFilename(null);
          if (filename) writeScript(filename);
        }}
        onCancel={() => setOverwriteFilename(null)}
      />
    </div>
  );

  return <GenericDisplayLayout className="command-console" leftPanel={leftPanel} centerPanel={centerPanel} />;
};
