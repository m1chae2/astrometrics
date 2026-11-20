/**
 * @file StarProfilePanel.tsx
 * @description The star profile panel of the FITS viewer. It shows what the backend measured for the
 * star the user clicked: the width (FWHM) from the fit, a Clipped chip when the star is clipped, and one
 * plot shaped like a bell curve. The points are the real pixels along a straight cut through the star
 * (the angle is set with a slider). The curve is the fitted star shape, mirrored to both sides, with a
 * dashed line at half its height to show the width. The "Around the star" tab plots the library's ring (the
 * brightness by direction with the neighbor stars left out), the smooth curve of its significant Fourier terms,
 * and a small bar chart of the size of each term, with a one-line summary. When the Shapes overlay has measured
 * the image, a small line under the header gives the frame's star shapes and how the clicked star compares.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { InfoButton } from '../components/InfoButton';
import { Slider } from '../components/Slider';
import { StatusChip } from '../components/StatusChip';
import { Tabs, tabElementId, tabPanelElementId } from '../components/Tabs';
import { BasePlot } from '../components/plotting/BasePlot';
import { buildLayout, getVar, getVarAsNum } from '../utils/plotTools';
import type { PlotlyTrace } from '../utils/plotTools';
import type { ProfileFit, RingHarmonic, StarProfileMeasurement, StarShapesMeasurement } from '../types/backendTypes';
import type { StarProfileState } from './hooks/useStarProfile';
import { rebuildCut } from './utils/starCut';
import { describeFrameShapes, describeStarAgainstFrame } from './utils/starShapes';
import { describeRing, harmonicCurve, hasFewPixels, screenDirection } from './utils/starRing';
import './starProfilePanel.css';

/** The words of the angle's info button. */
export const CUT_ANGLE_EXPLANATION = 'The dashed line on the image is the cut that is plotted. 0° runs straight up and down the image; 90° runs left to right.';

/** The message for an image that holds no pixel values. */
export const SET_DISPLAY_TO_LINEAR_MESSAGE = 'Set Display to Linear to measure stars here.';

/** The caption above the plot, which ties it to the dashed line on the image. */
export const PLOT_CAPTION = 'Pixels along the dashed line';

/** The note shown when some direction bins hold few pixels. */
export const FEW_PIXELS_NOTE = 'Few pixels per direction this close in';

/** The tooltip of the ring summary: how the percentages are made. */
export const RING_SUMMARY_TOOLTIP = 'Size of each pattern as a percent of the ring\'s mean brightness, with the bias that noise adds removed, and its 1 sigma uncertainty. Only patterns above the significance threshold are listed.';

/**
 * Says how many neighbor stars were left out of the ring.
 *
 * @param count The number of neighbors masked.
 * @returns For example "2 neighbors left out", "1 neighbor left out" or "No neighbors left out".
 */
export function describeNeighbors(count: number): string {
  if (count === 0) return 'No neighbors left out';
  return `${count} ${count === 1 ? 'neighbor' : 'neighbors'} left out`;
}

/** Shortest and longest cut half-length the slider offers, in pixels. */
export const CUT_LENGTH_RANGE = { min: 20, max: 100 } as const;

/** What the image needs to draw while the panel is open. */
export interface StarProfileView {
  /** "line" for the cut line, "ring" for the two circles of the "Around the star" tab. */
  mode: 'line' | 'ring';
  /** How far the cut reaches each way, in pixels, as drawn. */
  cutHalfLengthPx: number;
  /** Inner radius of the ring, in pixels. */
  ringInnerPx: number;
  /** Outer radius of the ring, in pixels. */
  ringOuterPx: number;
}

/** The tooltip of the log scale toggle. */
export const LOG_SCALE_TOOLTIP = 'Shows the faint wings; a one-sided flare shows as one tail higher than the other';

/**
 * Writes a number with a fixed count of decimals, or a dash when there is none.
 *
 * @param value The number.
 * @param decimals How many decimals to keep.
 * @returns The text.
 */
export function formatMeasurement(value: number | null | undefined, decimals = 2): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '-' : value.toFixed(decimals);
}

/**
 * Picks the fit that is drawn and quoted: the Moffat fit, or the Gaussian fit when the Moffat fit failed.
 *
 * @param measurement The backend's answer.
 * @returns The fit and its name, or null when both failed.
 */
export function chooseFit(measurement: StarProfileMeasurement): { fit: ProfileFit; name: 'Moffat' | 'Gaussian' } | null {
  // A profile that is not a star has no meaningful fit to quote or draw.
  if (!measurement.star_found) return null;
  if (measurement.moffat.fitted) return { fit: measurement.moffat, name: 'Moffat' };
  if (measurement.gaussian.fitted) return { fit: measurement.gaussian, name: 'Gaussian' };
  return null;
}

/**
 * Writes the width line, for example "FWHM 2.01 px (3.85″)" or, for the Gaussian fallback, "FWHM 2.01 px (3.85″) (Gaussian)".
 *
 * @param measurement The backend's answer.
 * @returns The line, or the reason both fits failed.
 */
export function describeWidth(measurement: StarProfileMeasurement): string {
  if (!measurement.star_found) return measurement.not_a_star_reason ?? 'This does not look like a star.';
  const chosen = chooseFit(measurement);
  if (!chosen) {
    return measurement.moffat.reason ?? measurement.gaussian.reason ?? 'The star shape could not be fitted.';
  }
  const arcsecond = chosen.fit.fwhm_arcsec !== null && chosen.fit.fwhm_arcsec !== undefined
    ? ` (${formatMeasurement(chosen.fit.fwhm_arcsec)}″)`
    : '';
  const fallback = chosen.name === 'Gaussian' ? ' (Gaussian)' : '';
  return `FWHM ${formatMeasurement(chosen.fit.fwhm_px)} px${arcsecond}${fallback}`;
}

/**
 * Writes the line that says how the star was found on a new image.
 *
 * @param method The method the backend reports.
 * @returns The line, for example "Followed by sky position".
 */
export function describeFollow(method: string): string {
  if (method === 'sky') return 'Followed by sky position';
  if (method === 'stack alignment') return 'Followed by stack alignment';
  if (method === 'stack alignment + sky') return 'Followed by stack alignment and sky position';
  return 'Same pixel (guess)';
}

/** Props for {@link StarProfilePanel}. */
export interface StarProfilePanelProps {
  /** What the panel shows. A closed state draws nothing. */
  state: StarProfileState;
  /** The angle of the cut now in use, in degrees. */
  cutAngleDegrees: number;
  /** Called with the new angle on every move of the slider. */
  onCutAngleChange: (degrees: number) => void;
  /** Called when the user closes the panel. */
  onClose: () => void;
  /** A sentence about the picture itself, shown as a short line (for example, that it is already stretched). */
  pictureNote?: string;
  /** The cut half-length the user chose, or null to use the measurement's own. */
  cutLengthPx?: number | null;
  /** Called with the new cut half-length on every move of its slider. */
  onCutLengthChange?: (lengthPx: number) => void;
  /** How the star was found after the image changed; null for a star the user clicked. */
  followNote?: { method: string; note: string } | null;
  /** Called when what the image should draw changes (the tab, the cut length, the ring). */
  onViewChange?: (view: StarProfileView) => void;
  /** The star shapes of the image, when the Shapes overlay has measured them; adds a frame summary line. */
  shapes?: StarShapesMeasurement | null;
}

/**
 * Builds the "FWHM" label beside the end of the dashed half-max line, where the steep sides of the curve
 * do not cross it. It goes to the right of the right end, or to the left of the left end when the right end
 * is in the right quarter of the plot.
 *
 * @param fwhm The width, in pixels.
 * @param halfMaximum Height of the dashed line, in data units.
 * @param halfRange The plot reaches from minus this to plus this on the distance axis.
 * @param logScale True when the brightness axis is logarithmic (annotation heights are then powers of ten).
 * @returns The Plotly annotation.
 */
function labelFwhm(fwhm: number, halfMaximum: number, halfRange: number, logScale: boolean): Record<string, unknown> {
  const rightEnd = fwhm / 2;
  const onLeft = rightEnd > halfRange / 2;
  return {
    x: onLeft ? -rightEnd : rightEnd,
    y: logScale ? Math.log10(halfMaximum) : halfMaximum,
    text: `FWHM ${formatMeasurement(fwhm)} px`,
    showarrow: false,
    xanchor: onLeft ? 'right' : 'left',
    yanchor: 'middle',
    xshift: onLeft ? -6 : 6,
    bgcolor: getVar('--surface-menu', '#2e2e32'),
    borderwidth: 0,
    font: { size: getVarAsNum('--small-font-size', 11), color: getVar('--plot-reference-line', '#aaaaaa') },
  };
}

/**
 * Draws the bell-curve plot: the pixels along the cut as points, the fitted shape mirrored to both
 * sides as a line, and a dashed line at half the peak that shows the width.
 *
 * @param props The measurement, whether the brightness axis is logarithmic, and the angle of the cut.
 * @returns The plot.
 */
function ProfilePlot({ measurement, logScale, angleDegrees, halfLengthPx }: { measurement: StarProfileMeasurement; logScale: boolean; angleDegrees: number; halfLengthPx: number }): React.ReactElement {
  const chosen = chooseFit(measurement);
  const { traces, halfRange, halfMaximum, fwhm, floorValue, highestValue } = useMemo(() => {
    // A log axis cannot draw zero or less, so those values are raised to a small floor: the sky noise, or a tiny part of the peak.
    const floor = measurement.sky_noise > 0 ? measurement.sky_noise : Math.abs(measurement.peak) * 1e-4;
    const lift = (values: number[]): number[] => (logScale ? values.map((value) => Math.max(value, floor)) : values);
    // The cut is rebuilt in the browser from the cutout, so it follows the slider with no call to the backend.
    const cut = rebuildCut(measurement, angleDegrees, halfLengthPx);
    const list: PlotlyTrace[] = [{
      x: cut.distance,
      y: lift(cut.values),
      type: 'scatter',
      mode: 'markers',
      marker: { color: getVar('--text-dim', '#aaaaaa'), size: 5 },
      hovertemplate: 'x %{x:.2f} px, %{y:.3g}<extra></extra>',
      name: 'pixels along the cut',
    }];
    let reach = Math.max(1, halfLengthPx);
    let half: number | null = null;
    let width: number | null = null;
    const radii = chosen?.fit.curve_radius_px ?? [];
    const values = chosen?.fit.curve_values ?? [];
    if (chosen && radii.length > 0 && radii.length === values.length) {
      // The fit is a function of the distance from the center, so the left side is the right side turned around.
      const mirroredX = [...radii].reverse().map((radius) => 0 - radius).concat(radii);
      const mirroredY = lift([...values].reverse().concat(values));
      list.push({
        x: mirroredX, y: mirroredY, type: 'scatter', mode: 'lines',
        line: { color: getVar('--plot-accent', '#3584e4'), width: 2 }, name: 'fitted shape',
        hovertemplate: 'fitted shape: %{y:.3g} at %{x:.2f} px<extra></extra>',
      });
      reach = Math.max(reach, ...radii);
      const peak = Math.max(...values);
      if (chosen.fit.fwhm_px !== null && chosen.fit.fwhm_px !== undefined) {
        half = Math.max(peak / 2, logScale ? floor : -Infinity);
        width = chosen.fit.fwhm_px;
        list.push({
          x: [-width / 2, width / 2], y: [half, half], type: 'scatter', mode: 'lines',
          line: { color: getVar('--plot-reference-line', '#aaaaaa'), width: 1.5, dash: 'dash' }, name: 'FWHM',
          hoverinfo: 'skip',
        });
      }
    }
    const plotted = list.flatMap((trace) => trace.y).filter((value) => Number.isFinite(value));
    return {
      traces: list, halfRange: reach, halfMaximum: half, fwhm: width, floorValue: floor, highestValue: Math.max(floor, ...plotted),
    };
  }, [measurement, chosen, logScale, angleDegrees, halfLengthPx]);
  const layout = useMemo(() => {
    const base = buildLayout({
      xTitle: 'distance through the star (px)',
      yTitle: logScale ? 'brightness above sky, log (raised to the sky noise)' : 'brightness above sky',
      margin: { l: 56, r: 10, t: 8, b: 44 },
    });
    return {
      ...base,
      xaxis: { ...base.xaxis, range: [-halfRange, halfRange], zeroline: true },
      // A log axis takes its range, and its annotations' positions, as powers of ten (log10 of the data values), not as data values.
      yaxis: logScale
        ? { ...base.yaxis, type: 'log', autorange: false, range: [Math.log10(floorValue), Math.log10(highestValue * 1.5)] }
        : { ...base.yaxis, type: 'linear' },
      showlegend: false,
      annotations: halfMaximum !== null && fwhm !== null ? [labelFwhm(fwhm, halfMaximum, halfRange, logScale)] : [],
    };
  }, [halfRange, halfMaximum, fwhm, logScale, floorValue, highestValue]);
  return (
    <div className="star-profile__plot-area">
      <BasePlot
        data={traces}
        layout={layout}
        config={{ responsive: true, displayModeBar: false }}
        data-testid="star-profile-plot"
      />
    </div>
  );
}

/**
 * Draws the plot of the "Around the star" view: the mean brightness in the ring by direction (from the library,
 * with the neighbor stars left out), and the smooth curve of the constant plus every significant Fourier term.
 *
 * @param props The measurement, the axis kind, the cut angle and the picture's row order.
 * @returns The plot.
 */
function RingPlot({ measurement, logScale, angleDegrees, firstRowAtTop }: {
  measurement: StarProfileMeasurement; logScale: boolean; angleDegrees: number; firstRowAtTop: boolean;
}): React.ReactElement {
  const { traces, floorValue, highestValue, fewPixels } = useMemo(() => {
    const floor = measurement.sky_noise > 0 ? measurement.sky_noise : Math.abs(measurement.peak) * 1e-4;
    const lift = (values: number[]): number[] => (logScale ? values.map((value) => Math.max(value, floor)) : values);
    const values = lift(measurement.ring_bin_values);
    const list: PlotlyTrace[] = [{
      x: measurement.ring_bin_angle_degrees, y: values, type: 'scatter', mode: 'lines+markers',
      line: { color: getVar('--plot-accent', '#3584e4'), width: 1.5 }, marker: { color: getVar('--text-dim', '#aaaaaa'), size: 4 },
      hovertemplate: '%{x:.0f}°: %{y:.3g}<extra></extra>', name: 'around the star',
    }];
    const curve = harmonicCurve(measurement);
    const curveValues = curve ? lift(curve.values) : [];
    if (curve) {
      list.push({
        x: curve.angles, y: curveValues, type: 'scatter', mode: 'lines',
        line: { color: getVar('--plot-orange', '#e5a50a'), width: 2 }, name: 'significant terms',
        hovertemplate: 'fitted: %{y:.3g} at %{x:.0f}°<extra></extra>',
      });
    }
    const fewPixels = hasFewPixels(measurement.ring_bin_counts, measurement.ring_bin_angle_degrees);
    return { traces: list, floorValue: floor, highestValue: Math.max(floor, ...values, ...curveValues), fewPixels };
  }, [measurement, logScale]);
  const layout = useMemo(() => {
    const base = buildLayout({
      xTitle: 'direction around the star (degrees)',
      yTitle: logScale ? 'brightness above sky, log (raised to the sky noise)' : 'brightness above sky',
      margin: { l: 56, r: 10, t: 8, b: 56 },
    });
    const partner = (angleDegrees + 180) % 360;
    const markerLine = (angle: number) => ({
      type: 'line', xref: 'x', yref: 'paper', x0: angle, x1: angle, y0: 0, y1: 1,
      line: { color: getVar('--plot-reference-line', '#aaaaaa'), width: 1, dash: 'dot' },
    });
    const ticks = [0, 90, 180, 270, 360];
    return {
      ...base,
      xaxis: {
        ...base.xaxis,
        range: [0, 360],
        tickvals: ticks,
        ticktext: ticks.map((angle) => `${screenDirection(angle, firstRowAtTop)}<br>${angle}°`),
      },
      yaxis: logScale
        ? { ...base.yaxis, type: 'log', autorange: false, range: [Math.log10(floorValue), Math.log10(highestValue * 1.5)] }
        : { ...base.yaxis, type: 'linear' },
      shapes: [markerLine(angleDegrees), markerLine(partner)],
      showlegend: false,
    };
  }, [logScale, angleDegrees, firstRowAtTop, floorValue, highestValue]);
  return (
    <div className="star-profile__plot-area star-profile__plot-area--ring">
      <BasePlot data={traces} layout={layout} config={{ responsive: true, displayModeBar: false }} data-testid="star-profile-ring-plot" />
      {fewPixels && <span className="star-profile__ring-note" data-testid="star-profile-ring-note">{FEW_PIXELS_NOTE}</span>}
    </div>
  );
}

/**
 * Draws the small bar chart of the Fourier terms of the ring: the size of each order as a percent of the ring's
 * mean brightness (with the noise bias removed), with a 1 sigma error bar. Terms that reach the significance
 * threshold are drawn in the accent color, the others dim.
 *
 * @param props The library's Fourier terms.
 * @returns The plot, or null when the ring was not analyzed.
 */
function HarmonicBars({ harmonics }: { harmonics: RingHarmonic[] }): React.ReactElement | null {
  const traces = useMemo(() => {
    const accent = getVar('--plot-accent', '#3584e4');
    const dim = getVar('--text-dim', '#aaaaaa');
    const trace: PlotlyTrace = {
      x: harmonics.map((term) => term.order),
      y: harmonics.map((term) => term.amplitude_debiased * 100),
      type: 'bar',
      marker: { color: harmonics.map((term) => (term.significant ? accent : dim)) },
      error_y: {
        type: 'data', array: harmonics.map((term) => term.amplitude_error * 100), visible: true,
        color: getVar('--plot-reference-line', '#aaaaaa'), thickness: 1.5, width: 3,
      },
      hovertemplate: 'order %{x}: %{y:.1f}%<extra></extra>',
      name: 'size of each term',
    };
    return [trace];
  }, [harmonics]);
  const layout = useMemo(() => {
    const base = buildLayout({ xTitle: 'Fourier order (lobes around the ring)', yTitle: 'size (%)', margin: { l: 56, r: 10, t: 4, b: 40 } });
    return {
      ...base,
      xaxis: { ...base.xaxis, tickvals: harmonics.map((term) => term.order), range: [0.4, harmonics.length + 0.6] },
      yaxis: { ...base.yaxis, rangemode: 'tozero' },
      showlegend: false,
      bargap: 0.3,
    };
  }, [harmonics]);
  if (harmonics.length === 0) return null;
  return (
    <div className="star-profile__plot-area star-profile__plot-area--bars">
      <BasePlot data={traces} layout={layout} config={{ responsive: true, displayModeBar: false }} data-testid="star-profile-harmonics-plot" />
    </div>
  );
}

/**
 * Shows the star profile panel.
 *
 * @param props See {@link StarProfilePanelProps}.
 * @returns The panel, or null when it is closed.
 */
export const StarProfilePanel: React.FC<StarProfilePanelProps> = ({
  state, cutAngleDegrees, onCutAngleChange, onClose, pictureNote, cutLengthPx = null, onCutLengthChange, onViewChange, followNote = null, shapes = null,
}) => {
  // Kept while the panel is open, and for the next star.
  const [logScale, setLogScale] = useState(false);
  const [tabId, setTabId] = useState<'line' | 'ring'>('line');
  const measurement = state.status === 'ready' ? state.measurement : state.status === 'measuring' ? state.previous : null;
  const anchor = state.status === 'ready' || state.status === 'measuring' ? state.anchor : null;
  // The cutout reaches only as far as the length the star was measured with; a longer cut waits for a new measurement.
  const cutHalfLengthPx = measurement ? Math.min(cutLengthPx ?? measurement.cut_half_length_px, measurement.cut_half_length_px) : 0;
  // The ring's radii come from the library, which sized them from the measured pixels.
  const ringLimits = useMemo(
    () => ({ innerPx: measurement?.ring_inner_px ?? 8, outerPx: measurement?.ring_outer_px ?? 20 }),
    [measurement],
  );

  // Tell the image what to draw.
  useEffect(() => {
    if (!measurement) return;
    onViewChange?.({ mode: tabId, cutHalfLengthPx, ringInnerPx: ringLimits.innerPx, ringOuterPx: ringLimits.outerPx });
  }, [measurement, tabId, cutHalfLengthPx, ringLimits, onViewChange]);

  if (state.status === 'closed') return null;

  let message: React.ReactNode = null;
  if (state.status === 'unavailable') {
    message = <p className="star-profile__message" data-testid="star-profile-message">{state.message}</p>;
  } else if (state.status === 'error') {
    message = <p className="star-profile__message star-profile__message--error" role="alert" data-testid="star-profile-error">{state.message}</p>;
  } else if (!measurement) {
    message = <p className="star-profile__message" data-testid="star-profile-measuring">Measuring the star.</p>;
  }

  // Under the header when the Shapes overlay has measured the image: the frame's summary, and how this star compares.
  const frameShapes = shapes ? describeFrameShapes(shapes) : null;
  const starAgainstFrame = shapes && measurement ? describeStarAgainstFrame(shapes, measurement.center_x, measurement.center_y) : null;
  const notes = [measurement?.color_note, pictureNote].filter(Boolean).join(' ');

  return (
    <aside className="star-profile" aria-label="Star profile" data-testid="star-profile-panel">
      <div className="star-profile__header">
        {measurement ? (
          <span
            className={`star-profile__width${measurement.star_found ? '' : ' star-profile__width--plain'}`}
            data-testid="star-profile-width"
          >{describeWidth(measurement)}</span>
        ) : (
          <span className="star-profile__title">Star profile</span>
        )}
        {measurement?.clipped && <StatusChip status="warning" label="Clipped" title={measurement.clipped_reason} />}
        <Button variant="ghost" size="square" onClick={onClose} aria-label="Close star profile" title="Close star profile">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
          </svg>
        </Button>
      </div>
      {followNote && <p className="star-profile__note" title={followNote.note} data-testid="star-profile-follow">{describeFollow(followNote.method)}</p>}
      {frameShapes && (
        <p className="star-profile__note" data-testid="star-profile-frame-shapes">
          {frameShapes}
          {starAgainstFrame && <><br />{starAgainstFrame}</>}
        </p>
      )}
      {message}
      {measurement && (
        <div className={`star-profile__body${state.status === 'measuring' ? ' star-profile__body--updating' : ''}`}>
          {notes && <p className="star-profile__note" data-testid="star-profile-note">{notes}</p>}
          <Tabs
            idPrefix="star-profile"
            ariaLabel="Star profile views"
            size="compact"
            selectedId={tabId}
            onChange={(id) => setTabId(id === 'ring' ? 'ring' : 'line')}
            tabs={[{ id: 'line', label: 'Along the line' }, { id: 'ring', label: 'Around the star' }]}
          />
          <div
            className="star-profile__tabpanel"
            role="tabpanel"
            id={tabPanelElementId('star-profile', tabId)}
            aria-labelledby={tabElementId('star-profile', tabId)}
          >
            <div className="star-profile__plot-heading">
              <span className="star-profile__caption">{tabId === 'line' ? PLOT_CAPTION : 'Mean brightness in the ring, by direction'}</span>
              <Button size="small" pressed={logScale} variant={logScale ? 'default' : 'secondary'} title={LOG_SCALE_TOOLTIP} onClick={() => setLogScale(!logScale)}>
                Log scale
              </Button>
            </div>
            {tabId === 'line' ? (
              <ProfilePlot measurement={measurement} logScale={logScale} angleDegrees={cutAngleDegrees} halfLengthPx={cutHalfLengthPx} />
            ) : (
              <RingPlot
                measurement={measurement}
                logScale={logScale}
                angleDegrees={cutAngleDegrees}
                firstRowAtTop={anchor?.firstRowAtTop ?? false}
              />
            )}
            {tabId === 'ring' && (
              <>
                <HarmonicBars harmonics={measurement.ring_harmonics} />
                <p className="star-profile__note star-profile__summary" data-testid="star-profile-ring-summary" title={RING_SUMMARY_TOOLTIP}>
                  {measurement.ring_harmonics.length > 0
                    ? describeRing(measurement.ring_harmonics, anchor?.firstRowAtTop ?? false)
                    : (measurement.ring_note ?? '')}
                </p>
              </>
            )}
          </div>
          <div className="star-profile__angle">
            <span>{`Cut angle ${cutAngleDegrees}°`}</span>
            <Slider
              aria-label="Cut angle in degrees"
              min={0}
              max={179}
              step={1}
              value={cutAngleDegrees}
              onChange={(event) => onCutAngleChange(Number(event.target.value))}
            />
            <InfoButton text={CUT_ANGLE_EXPLANATION} label="About the cut angle" />
          </div>
          {tabId === 'line' ? (
            <div className="star-profile__angle">
              <span>{`Cut length ${Math.round(cutLengthPx ?? measurement.cut_half_length_px)} px`}</span>
              <Slider
                aria-label="Cut length in pixels"
                min={CUT_LENGTH_RANGE.min}
                max={CUT_LENGTH_RANGE.max}
                step={1}
                value={Math.max(CUT_LENGTH_RANGE.min, cutLengthPx ?? measurement.cut_half_length_px)}
                onChange={(event) => onCutLengthChange?.(Number(event.target.value))}
              />
            </div>
          ) : (
            <div className="star-profile__angle">
              <span>{`Ring ${Math.round(ringLimits.innerPx)} to ${Math.round(ringLimits.outerPx)} px`}</span>
              <span data-testid="star-profile-ring-neighbors">{describeNeighbors(measurement.ring_masked_neighbor_count)}</span>
            </div>
          )}
        </div>
      )}
    </aside>
  );
};
