import * as vscode from 'vscode';

/**
 * VS Code extension entrypoint for Dev Flow helper.
 * - Provides a status bar toggle to start/stop the Vite+Electron dev flow
 * - Shows running task name and PID when available
 * - Listens to task start/end events to reflect accurate state
 */
function activate(context) {
  let statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = 'devflow.toggle';
  statusBar.text = '$(play) Start Dev';
  statusBar.tooltip = 'Start Vite + Electron (Dev)';
  statusBar.show();

  // Keep track of the current task execution/process id
  let currentExecution = null;
  let currentPid = null;

  async function startDev() {
    try {
      // Run the Start Electron task (it depends on Start Vite)
      await vscode.commands.executeCommand('workbench.action.tasks.runTask', 'Start Electron (dev)');
    } catch (err) {
      vscode.window.showErrorMessage('Failed to start dev tasks: ' + String(err));
    }
  }

  async function stopDev() {
    try {
      await vscode.commands.executeCommand('workbench.action.tasks.terminate');
    } catch (err) {
      vscode.window.showErrorMessage('Failed to stop dev tasks: ' + String(err));
    }
  }

  const startCmd = vscode.commands.registerCommand('devflow.start', startDev);
  const stopCmd = vscode.commands.registerCommand('devflow.stop', stopDev);
  const toggleCmd = vscode.commands.registerCommand('devflow.toggle', async () => {
    if (currentExecution) await stopDev();
    else await startDev();
  });

  // Listen for task process start to capture PID and execution info
  const startListener = vscode.tasks.onDidStartTaskProcess((e) => {
    currentExecution = e.execution;
    currentPid = e.processId;
    const taskName = (e.execution && e.execution.task && e.execution.task.name) || 'dev';
    statusBar.text = `$(debug-alt) Stop Dev (${taskName}${currentPid ? ' pid:' + currentPid : ''})`;
    statusBar.tooltip = `Stop running dev flow (task: ${taskName})`;
    statusBar.show();
  });

  // Listen for task process end to clear state
  const endListener = vscode.tasks.onDidEndTaskProcess((e) => {
    // If this was the current execution, clear
    const endedTaskName = (e.execution && e.execution.task && e.execution.task.name) || 'dev';
    currentExecution = null;
    currentPid = null;
    statusBar.text = '$(play) Start Dev';
    statusBar.tooltip = 'Start Vite + Electron (Dev)';
    vscode.window.showInformationMessage(`Dev flow task ended: ${endedTaskName}`);
  });

  // Watch for task start/finish events to show transition states
  const startedTaskListener = vscode.tasks.onDidStartTask((t) => {
    const name = t.execution && t.execution.task && t.execution.task.name;
    if (name) {
      statusBar.text = `$(sync~spin) Starting ${name}`;
      statusBar.tooltip = `Starting task ${name}`;
      statusBar.show();
    }
  });

  context.subscriptions.push(startCmd, stopCmd, toggleCmd, statusBar, startListener, endListener, startedTaskListener);
}

function deactivate() {
  // Nothing to cleanup — task termination is handled via commands
}

export { activate, deactivate };
