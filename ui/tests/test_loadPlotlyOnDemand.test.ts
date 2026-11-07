/**
 * @fileoverview Tests that Plotly is loaded on first use: one script tag is added,
 * later calls reuse it, and a failed load gives a clear error and can be retried.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('loadPlotly', () => {
  beforeEach(() => {
    vi.resetModules();
    delete (window as any).Plotly;
    document.head.querySelectorAll('script').forEach((script) => script.remove());
  });

  afterEach(() => {
    delete (window as any).Plotly;
    document.head.querySelectorAll('script').forEach((script) => script.remove());
  });

  /** Finds the script tags that load the Plotly file. */
  const plotlyScripts = (): HTMLScriptElement[] =>
    Array.from(document.head.querySelectorAll('script')).filter((script) =>
      script.getAttribute('src')?.includes('plotly.min.js')
    );

  it('adds one script tag and shares it between callers', async () => {
    const { loadPlotly } = await import('../common/utils/plotTools');
    const first = loadPlotly();
    const second = loadPlotly();
    expect(plotlyScripts()).toHaveLength(1);

    const fakePlotly = { newPlot: vi.fn() };
    (window as any).Plotly = fakePlotly;
    plotlyScripts()[0].dispatchEvent(new Event('load'));

    expect(await first).toBe(fakePlotly);
    expect(await second).toBe(fakePlotly);

    // Once Plotly exists, no new tag is added.
    expect(await loadPlotly()).toBe(fakePlotly);
    expect(plotlyScripts()).toHaveLength(1);
  });

  it('rejects with a clear error when the file cannot load, then allows a retry', async () => {
    const { loadPlotly } = await import('../common/utils/plotTools');
    const failed = loadPlotly();
    plotlyScripts()[0].dispatchEvent(new Event('error'));
    await expect(failed).rejects.toThrow(/Could not load the charting library/);

    const retry = loadPlotly();
    expect(plotlyScripts()).toHaveLength(1);
    (window as any).Plotly = { newPlot: vi.fn() };
    plotlyScripts()[0].dispatchEvent(new Event('load'));
    await expect(retry).resolves.toBeDefined();
  });
});
