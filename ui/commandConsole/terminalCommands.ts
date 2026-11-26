/**
 * @file terminalCommands.ts
 * @description The few commands the Command Console's terminal handles itself, before a line
 * goes to Python, and the requests console code sends back to the app. `clear` clears the
 * screen, `clear('workspace')` clears your variables, and `doc name` is turned into the Python
 * call `doc("name")`. Only a line that is exactly one of these commands counts, so a variable
 * you name `clear` still works in Python. A `show(...)` request becomes the same navigation
 * request the app's own buttons send, so another window that shows that display handles it.
 */
import type { ConsoleAppRequest } from '../common/services/backendApi';
import type { NavigationIntent } from '../common/utils/displayCoordinator';

/** What one line typed at the terminal prompt asks for. */
export type TerminalCommand =
  | { kind: 'clear-terminal' }
  | { kind: 'clear-workspace' }
  | { kind: 'clear-usage' }
  | { kind: 'python'; code: string };

/** The short line printed when `clear(...)` gets an argument it does not know. */
export const CLEAR_USAGE = "Type clear to clear the screen, or clear('workspace') to clear your variables.";

/** The one dim line at the top of the terminal. */
export const TERMINAL_INTRO = "Python 3.14 · type help for the preloaded names · clear clears the screen · doc name opens its documentation";

/** `clear` or `clear()`. */
const CLEAR_SCREEN = /^clear\s*(\(\s*\))?$/;
/** `clear('workspace')` or `clear("workspace")`. */
const CLEAR_WORKSPACE = /^clear\s*\(\s*(['"])workspace\1\s*\)$/;
/** `clear(...)` with any other argument. */
const CLEAR_OTHER = /^clear\s*\(.*\)$/;
/** `doc name` or `doc(name)`, where the name is a dotted Python name. */
const DOC_NAME = /^doc(?:\s+([A-Za-z_][\w.]*)|\s*\(\s*([A-Za-z_][\w.]*)\s*\))$/;

/**
 * Reads one line typed at the terminal prompt.
 *
 * @param line The line as typed.
 * @returns What it asks for: a terminal command, or Python code to run (a `doc name` line is
 *   turned into `doc("name")`, so the backend looks the name up through your variables too).
 */
export function parseTerminalCommand(line: string): TerminalCommand {
  const trimmed = line.trim();
  if (CLEAR_SCREEN.test(trimmed)) return { kind: 'clear-terminal' };
  if (CLEAR_WORKSPACE.test(trimmed)) return { kind: 'clear-workspace' };
  if (CLEAR_OTHER.test(trimmed)) return { kind: 'clear-usage' };
  const docMatch = DOC_NAME.exec(trimmed);
  if (docMatch) return { kind: 'python', code: `doc(${JSON.stringify(docMatch[1] ?? docMatch[2])})` };
  return { kind: 'python', code: line };
}

/** A `show(...)` request from console code. */
export type ShowRequest = Extract<ConsoleAppRequest, { kind: 'show' }>;

/**
 * Builds the navigation request for one `show(...)` call, in the shape the app's own buttons use:
 * Image Processing opens the target (with the star selected on its stack, or with the file shown),
 * and the Astronomy Manager opens the star.
 *
 * @param request The request the backend returned.
 * @returns The navigation request for `navigateToElement`.
 */
export function showRequestIntent(request: ShowRequest): NavigationIntent {
  if (request.display === 'Astronomy Manager') {
    return {
      targetDisplay: 'Astronomy Manager',
      targetElement: 'spectrumViewer',
      action: 'astronomySelectStar',
      payload: { starId: request.star_id, targetId: request.target ?? undefined },
      toast: { message: `Opening ${request.star_name ?? request.star_id} in Astronomy Manager`, type: 'info', title: 'Command Console' },
    };
  }
  let payload: Record<string, unknown> = { targetId: request.target };
  if (request.star_id) {
    payload = { targetId: request.target, enableAstrometry: true, focusLibraryStarId: request.star_id, focusStarName: request.star_name ?? '' };
  } else if (request.path) {
    payload = { targetId: request.target, filePath: request.path };
  }
  return {
    targetDisplay: 'Image Processing',
    targetElement: 'fitsViewer',
    action: 'targetSelected',
    payload,
    toast: { message: `Opening ${request.target} in Image Processing`, type: 'info', title: 'Command Console' },
  };
}

/**
 * Formats a run's elapsed time for the running line.
 *
 * @param elapsedMs Milliseconds since the run started.
 * @returns Such as "0:07" or "12:30".
 */
export function formatElapsed(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}
