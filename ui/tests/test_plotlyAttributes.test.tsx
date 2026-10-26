/**
 * @fileoverview Checks that every chart the app draws uses only settings that
 * the bundled Plotly version understands.
 *
 * Plotly quietly ignores a setting it does not know (for example one removed in
 * a new major version), so a chart can look wrong with no error at all. These
 * tests render the real chart components with a fake `window.Plotly` that
 * records what they ask for, then hand those settings to the real Plotly's
 * `validate()` check, which lists every unknown or bad setting.
 *
 * The app loads Plotly from `public/js/plotly.min.js`, not from the npm
 * package, so the tests also check that the two copies are the same version.
 */

import fs from 'fs';
import path from 'path';
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest';
import { render, waitFor, cleanup } from '@testing-library/react';

import { buildLayout } from '../common/utils/plotTools';
import { TimeSeriesPlot } from '../common/components/plotting/TimeSeriesPlot';
import { ScatterPlot } from '../common/components/plotting/ScatterPlot';
import { SpectrumViewer } from '../astronomyManager/components/SpectrumViewer';
import { PhotometryViewer } from '../astronomyManager/components/PhotometryViewer';

/** One problem reported by `Plotly.validate()`. */
interface PlotlyValidationError {
  code: string;
  container: string;
  path: string | (string | number)[];
  msg: string;
}

/** The small part of the real Plotly library these tests use. */
interface RealPlotly {
  version: string;
  validate(data: unknown[], layout: unknown): PlotlyValidationError[] | undefined;
}

/** What a chart component passed to `newPlot()`. */
interface RecordedPlot {
  data: unknown[];
  layout: Record<string, any>;
  config: Record<string, any>;
}

const repoRoot = path.resolve(__dirname, '..', '..');
const shippedPlotlyPath = path.join(repoRoot, 'public', 'js', 'plotly.min.js');
const npmPlotlyPackagePath = path.join(repoRoot, 'node_modules', 'plotly.js-dist-min', 'package.json');

let realPlotly: RealPlotly;
let recorded: RecordedPlot[] = [];

/**
 * Runs Plotly's own check on a chart's traces and layout.
 * @param plot The traces and layout a component asked for.
 * @returns A readable line for each problem found; empty when all is well.
 */
function problemsWith(plot: RecordedPlot): string[] {
  const errors = realPlotly.validate(plot.data, plot.layout) ?? [];
  return errors.map((e) => `${e.container} ${Array.isArray(e.path) ? e.path.join('.') : e.path}: ${e.msg}`);
}

/**
 * Waits until a component has drawn a chart, then returns the last one drawn.
 * @returns The traces, layout and config of the most recent `newPlot()` call.
 */
async function lastPlot(): Promise<RecordedPlot> {
  await waitFor(() => expect(recorded.length).toBeGreaterThan(0));
  return recorded[recorded.length - 1];
}

beforeAll(() => {
  // Load the exact file the app ships. Its wrapper hands the library to
  // `module.exports` when a `module` object is passed in, so running it this
  // way does not touch the fake `window.Plotly` used below.
  const loaded: { exports: unknown } = { exports: {} };
  new Function('module', 'exports', fs.readFileSync(shippedPlotlyPath, 'utf8'))(loaded, loaded.exports);
  realPlotly = loaded.exports as RealPlotly;
});

beforeEach(() => {
  recorded = [];
  /** Fake Plotly that records each chart instead of drawing it. */
  const record = async (_node: HTMLElement, data: unknown[], layout?: any, config?: any) => {
    recorded.push({ data, layout: layout ?? {}, config: config ?? {} });
  };
  (window as any).Plotly = {
    newPlot: record,
    react: record,
    addTraces: async () => undefined,
    deleteTraces: async () => undefined,
    purge: () => undefined,
  };
});

afterEach(() => {
  cleanup();
  delete (window as any).Plotly;
});

describe('bundled Plotly copy', () => {
  /** The shipped file must be the same release as the npm package in package.json. */
  it('matches the plotly.js-dist-min npm package version', () => {
    const npmVersion = JSON.parse(fs.readFileSync(npmPlotlyPackagePath, 'utf8')).version;
    expect(realPlotly.version).toBe(npmVersion);
    expect(fs.readFileSync(shippedPlotlyPath, 'utf8').slice(0, 200)).toContain(`plotly.js v${npmVersion}`);
  });
});

describe('chart settings are all known to Plotly', () => {
  /** The shared dark layout used by the photometry and spectrum views. */
  it('buildLayout gives a valid layout', () => {
    const plot = { data: [{ type: 'scatter', x: [1, 2], y: [3, 4] }], layout: buildLayout({ xTitle: 'A', yTitle: 'B' }), config: {} };
    expect(problemsWith(plot)).toEqual([]);
  });

  /** The guiding trends chart, including its second (SNR) axis on the right. */
  it('TimeSeriesPlot with a second y axis', async () => {
    render(
      <TimeSeriesPlot
        data={[
          { name: 'dRA', color: '#00ffff', values: [0.1, -0.2], timestamps: [1700000000, 1700000002] },
          { name: 'SNR', color: '#34c759', values: [40, 42], timestamps: [1700000000, 1700000002], yaxis: 'y2' },
        ]}
        yLabel="arcsec"
        yRange={[-2, 2]}
        showLegend
      />
    );
    const plot = await lastPlot();
    expect(problemsWith(plot)).toEqual([]);
    // Plotly 4 changed the default tick spacing of an overlaid axis to 'sync';
    // the SNR axis keeps its own ticks, as under Plotly 3.
    expect(plot.layout.yaxis2.tickmode).toBe('auto');
    expect(plot.config.displayModeBar).toBe(false);
  });

  /** The night altitude chart: shaded bands with labels, a limit line and local time. */
  it('TimeSeriesPlot with bands and a reference line', async () => {
    render(
      <TimeSeriesPlot
        data={[{ name: 'Altitude', color: '#00ffff', values: [30, 60], timestamps: [1700000000, 1700003600] }]}
        yLabel="deg"
        yRange={[-30, 90]}
        bands={[{ start: 1700000000, end: 1700001800, label: 'Usable', color: '#34c759' }]}
        referenceLines={[{ y: 30, label: 'Altitude limit 30 deg', color: '#ffb300' }]}
        localTime
      />
    );
    const plot = await lastPlot();
    expect(problemsWith(plot)).toEqual([]);
    expect(plot.layout.shapes).toHaveLength(2);
    expect(plot.layout.annotations).toHaveLength(2);
  });

  /** The guiding bullseye chart with its three target circles. */
  it('ScatterPlot with bullseye circles', async () => {
    render(<ScatterPlot data={[{ x: [0.5], y: [-0.5], name: 'Guide Error', color: '#ffffff' }]} xRange={[5, -5]} yRange={[-5, 5]} showCircles />);
    const plot = await lastPlot();
    expect(problemsWith(plot)).toEqual([]);
  });

  /** A spectrum with a detected, an inconclusive and a possible feature line and their labels. */
  it('SpectrumViewer with feature lines and labels', async () => {
    const features = [
      { feature: 'H-alpha', wavelength_angstrom: 6563, measured_wavelength_angstrom: 6561, verdict: 'detected', depth: 0.3, p_value: 0.001, probability_present: 0.9 },
      { feature: 'H-beta', wavelength_angstrom: 4861, verdict: 'inconclusive' },
      { feature: 'Na D', wavelength_angstrom: 5893, verdict: 'possible', depth: 0.1 },
    ];
    const astronomyData = {
      wavelength: [4000, 5000, 6000, 7000],
      spectrumFlux: [1, 0.8, 0.9, 1.1],
      spectroscopy: { probableSpectralFeatures: features, emissionLines: [] },
    } as any;
    render(<SpectrumViewer astronomyData={astronomyData} loading={false} />);
    const plot = await lastPlot();
    expect(plot.layout.shapes.length).toBe(3);
    expect(plot.layout.annotations.length).toBe(3);
    expect(problemsWith(plot)).toEqual([]);
  });

  /** A magnitude light curve, which turns the y axis upside down. */
  it('PhotometryViewer with magnitudes', async () => {
    const astronomyData = {
      photometry: {
        timestamps: ['2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z'],
        magnitudes: [10.1, 10.3],
      },
    } as any;
    render(<PhotometryViewer astronomyData={astronomyData} loading={false} />);
    const plot = await lastPlot();
    expect(plot.layout.yaxis.autorange).toBe('reversed');
    expect(problemsWith(plot)).toEqual([]);
  });

  /** Proves the check really catches a setting Plotly 4 removed, so a pass above means something. */
  it('reports a removed setting', () => {
    const plot = { data: [{ type: 'scatter', x: [1], y: [1], stream: { token: 'x' } }], layout: {}, config: {} };
    expect(problemsWith(plot).some((line) => line.includes('stream'))).toBe(true);
  });
});
