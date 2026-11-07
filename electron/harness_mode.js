/**
 * @fileoverview Options that apply only when the technical performance measures (TPM) harness runs the app.
 *
 * The harness (`build/tpm/`) starts the real Electron app and sets `ASTROMETRICS_TPM_HARNESS=1`. Two things change
 * then, and nothing changes in a normal run:
 *  - the window's page is not throttled when it is hidden or covered (`backgroundThrottling: false`), so timers and
 *    animation frames run at full speed and a measurement is not slowed by another window;
 *  - the main window is shown without taking focus (`showInactive`), so a run on the real display does not steal
 *    the keyboard from what the user is doing.
 * The window is deliberately not moved off screen: a window that is fully covered or off screen can be skipped by
 * the window system's compositor, which changes what gets drawn. The default harness run uses an invisible screen
 * (xvfb), where no window reaches the desktop at all.
 */

/** The environment variable the harness sets to ask for these options. */
export const HARNESS_ENVIRONMENT_VARIABLE = 'ASTROMETRICS_TPM_HARNESS';

/**
 * Tells whether the harness started this app.
 *
 * @param {Record<string, string | undefined>} environment - The process environment.
 * @return {boolean} True when `ASTROMETRICS_TPM_HARNESS` is `1`.
 */
export function isHarnessRun(environment) {
  return environment[HARNESS_ENVIRONMENT_VARIABLE] === '1';
}

/**
 * Gives the web preferences to add to a window.
 *
 * @param {Record<string, string | undefined>} environment - The process environment.
 * @return {{backgroundThrottling?: boolean}} `{ backgroundThrottling: false }` in a harness run, otherwise nothing.
 */
export function harnessWebPreferences(environment) {
  return isHarnessRun(environment) ? { backgroundThrottling: false } : {};
}

/** The variable that gives a harness run's main window size, for example `1920x1080`. */
export const HARNESS_WINDOW_SIZE_VARIABLE = 'ASTROMETRICS_TPM_WINDOW_SIZE';

/**
 * Reads the window size a harness run asks for.
 *
 * @param {Record<string, string | undefined>} environment - The process environment.
 * @return {{width: number, height: number} | null} The size, or null when this is not a harness run or none is asked.
 */
export function harnessWindowSize(environment) {
  if (!isHarnessRun(environment)) return null;
  const match = /^(\d{3,5})x(\d{3,5})$/.exec(environment[HARNESS_WINDOW_SIZE_VARIABLE] ?? '');
  return match ? { width: Number(match[1]), height: Number(match[2]) } : null;
}

/**
 * Gives the window size to add to the main window's options.
 *
 * @param {Record<string, string | undefined>} environment - The process environment.
 * @return {{width?: number, height?: number}} The size a harness run asks for, otherwise nothing.
 */
export function harnessWindowOptions(environment) {
  return harnessWindowSize(environment) ?? {};
}

/**
 * Shows the main window, without taking focus in a harness run.
 *
 * @param {{show: () => void, showInactive: () => void}} window - The window to show.
 * @param {Record<string, string | undefined>} environment - The process environment.
 * @return {void}
 */
export function showMainWindow(window, environment) {
  if (isHarnessRun(environment)) window.showInactive();
  else window.show();
}
