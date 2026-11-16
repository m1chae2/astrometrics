/**
 * @fileoverview Keeps one window per figure.
 *
 * Running a script again, or two windows asking for the same figure, must not
 * pile up copies of one plot. This registry remembers which window shows
 * which figure. The key of a figure is its interactive page address
 * (`/figure/<number>` on the backend) or, for a plot that has only a picture,
 * the path of its PNG file. A request for a figure that is already open
 * reloads that window and brings it to the front. Otherwise a new window is
 * made, and it is forgotten when it closes.
 *
 * This file does not touch Electron itself, so it can be tested with plain
 * stand-in windows. `main.js` gives it the real windows.
 */

/**
 * Makes an empty registry of figure windows.
 *
 * @returns {{
 *   openOrReuse: (key: string, create: () => object, reload: (win: object) => void) => object,
 *   size: () => number,
 * }} The registry.
 */
export function createFigureWindowRegistry() {
  /** @type {Map<string, object>} Open figure windows by figure key. */
  const windowsByKey = new Map();

  return {
    /**
     * Returns the open window for a figure, or makes one.
     *
     * @param {string} key The figure's page address or PNG path.
     * @param {() => object} create Makes a new window that shows the figure.
     * @param {(win: object) => void} reload Redraws an existing window.
     * @returns {object} The window that shows the figure.
     */
    openOrReuse(key, create, reload) {
      const existing = windowsByKey.get(key);
      if (existing && !existing.isDestroyed()) {
        reload(existing);
        if (typeof existing.isMinimized === 'function' && existing.isMinimized()) {
          existing.restore();
        }
        existing.focus();
        return existing;
      }
      const created = create();
      windowsByKey.set(key, created);
      created.on('closed', () => {
        // A newer window may have taken the key since this one was made.
        if (windowsByKey.get(key) === created) {
          windowsByKey.delete(key);
        }
      });
      return created;
    },

    /**
     * Counts the figures that have a window.
     *
     * @returns {number} The number of remembered windows.
     */
    size() {
      return windowsByKey.size;
    },
  };
}
