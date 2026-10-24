/**
 * @file CameraProfilesPanel.tsx
 * @description The camera profiles from the settings, the measured gain and
 * read noise of each camera setting, and the measurement call. Until the
 * library returns measured noise with the profiles, the noise table shows
 * typed example values with an "Example data" badge. A measurement is shown
 * first; saving it to the calibration library is a local write, so the panel
 * asks before it saves.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { ExampleDataBadge } from '../../common/components/quality/ExampleDataBadge';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import { exampleCameraNoise } from '../../common/examples/exampleCameraNoise';
import { callBackend } from '../../common/services/backendApi';
import type { CameraProfile } from '../../common/hooks/useEquipmentConfiguration';
import { MeasurementStatus, type SensorNoiseMeasurement } from '../../common/types/backendTypes';
import '../../common/components/quality/quality.css';
import '../observatoryManager.css';

/** Properties of {@link CameraProfilesPanel}. */
export interface CameraProfilesPanelProps {
    /** Every camera profile in the settings. */
    cameras: CameraProfile[];
    /** Whether the first read is still running. */
    loading: boolean;
    /** The error text of a failed read, or null. */
    error: string | null;
    /** Reads the cameras again. */
    onRetry: () => void;
}

/**
 * Formats a number for display, or a dash when it is missing.
 * @param value The number.
 * @param digits Decimal places.
 * @return The text.
 */
function formatNumber(value: number | null | undefined, digits: number): string {
    return typeof value === 'number' ? value.toFixed(digits) : '—';
}

/**
 * Shows camera profiles, example noise values and the noise measurement.
 * @param props See {@link CameraProfilesPanelProps}.
 * @return The panel.
 */
export const CameraProfilesPanel: React.FC<CameraProfilesPanelProps> = ({ cameras, loading, error, onRetry }) => {
    const [cameraId, setCameraId] = useState('');
    const [gain, setGain] = useState('');
    const [offset, setOffset] = useState('');
    const [isMeasuring, setIsMeasuring] = useState(false);
    const [measurement, setMeasurement] = useState<SensorNoiseMeasurement | null>(null);
    const [measureError, setMeasureError] = useState<string | null>(null);
    const [isConfirmingSave, setIsConfirmingSave] = useState(false);
    const [saveResult, setSaveResult] = useState<string | null>(null);

    const chosenCamera = cameraId || cameras[0]?.name || '';
    const canMeasure = chosenCamera !== '' && gain.trim() !== '' && offset.trim() !== '' && !isMeasuring;

    /** Runs one measurement without saving it. */
    const measure = async () => {
        setIsMeasuring(true);
        setMeasureError(null);
        setMeasurement(null);
        setSaveResult(null);
        setIsConfirmingSave(false);
        try {
            setMeasurement(await callBackend('calibration:measure_sensor_noise', {
                camera_id: chosenCamera,
                gain: gain.trim(),
                offset: offset.trim(),
                save: false,
            }));
        } catch (caught) {
            setMeasureError(caught instanceof Error ? caught.message : 'The measurement failed.');
        } finally {
            setIsMeasuring(false);
        }
    };

    /** Measures again with saving on, after the user said yes. */
    const save = async () => {
        setIsConfirmingSave(false);
        setIsMeasuring(true);
        setMeasureError(null);
        try {
            const saved = await callBackend('calibration:measure_sensor_noise', {
                camera_id: chosenCamera,
                gain: gain.trim(),
                offset: offset.trim(),
                save: true,
            });
            setMeasurement(saved);
            setSaveResult(saved.saved ? 'Saved to the calibration library.' : 'The measurement was not saved.');
        } catch (caught) {
            setMeasureError(caught instanceof Error ? caught.message : 'The measurement could not be saved.');
        } finally {
            setIsMeasuring(false);
        }
    };

    let profiles: React.ReactNode;
    if (loading) {
        profiles = <QualityPanelState state="loading" />;
    } else if (error) {
        profiles = <QualityPanelState state="error" subject="camera profiles" onRetry={onRetry} />;
    } else if (cameras.length === 0) {
        profiles = <QualityPanelState state="empty" emptyMessage="No camera profiles. Add a camera in the settings." />;
    } else {
        profiles = (
            <table className="calibration-table" aria-label="Camera profiles">
                <thead>
                    <tr>
                        <th>Camera</th>
                        <th className="data-table-cell-right">Pixel size</th>
                        <th className="data-table-cell-right">Sensor</th>
                    </tr>
                </thead>
                <tbody>
                    {cameras.map((camera) => (
                        <tr key={camera.name}>
                            <td>{camera.name}</td>
                            <td className="data-table-cell-right">{camera.pixelSizeUm} μm</td>
                            <td className="data-table-cell-right">{camera.sensorWidthPx} × {camera.sensorHeightPx} px</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        );
    }

    return (
        <SectionPanel title="Camera Profiles">
            <div className="equipment-block">
                {profiles}

                <div data-example="true" data-testid="camera-noise-example">
                    <div className="equipment-block__row">
                        <strong>Measured noise</strong>
                        <ExampleDataBadge />
                    </div>
                    <table className="calibration-table" aria-label="Measured camera noise (example data)">
                        <thead>
                            <tr>
                                <th>Camera</th>
                                <th className="data-table-cell-right">Gain setting</th>
                                <th className="data-table-cell-right">Gain (e⁻/count)</th>
                                <th className="data-table-cell-right">Read noise (e⁻)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {exampleCameraNoise.map((row) => (
                                <tr key={`${row.cameraId}-${row.gainSetting}`}>
                                    <td>{row.cameraId}</td>
                                    <td className="data-table-cell-right">{row.gainSetting}</td>
                                    <td className="data-table-cell-right">{row.gainElectronsPerCount}</td>
                                    <td className="data-table-cell-right">{row.readNoiseElectrons}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <form
                    className="equipment-block__form"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (canMeasure) void measure();
                    }}
                >
                    <label>
                        Camera{' '}
                        <select className="dropdown" value={chosenCamera} onChange={(event) => setCameraId(event.target.value)}>
                            {cameras.map((camera) => <option key={camera.name} value={camera.name}>{camera.name}</option>)}
                        </select>
                    </label>
                    <label>
                        Gain{' '}
                        <input className="input input--short" value={gain} onChange={(event) => setGain(event.target.value)} />
                    </label>
                    <label>
                        Offset{' '}
                        <input className="input input--short" value={offset} onChange={(event) => setOffset(event.target.value)} />
                    </label>
                    <button type="submit" className="btn quality-button" disabled={!canMeasure} aria-disabled={!canMeasure} aria-describedby="measure-reason">
                        {isMeasuring ? 'Measuring…' : 'Measure noise'}
                    </button>
                </form>
                <p id="measure-reason" className="equipment-block__reason">
                    {canMeasure || isMeasuring
                        ? 'Measuring reads flat and bias frames in the library. It saves nothing until you say so.'
                        : 'Enter a camera, a gain and an offset to measure.'}
                </p>

                {measureError && (
                    <div className="quality-state quality-state--error" role="alert">
                        <span>{measureError}</span>
                    </div>
                )}
                {measurement && (
                    <div data-testid="noise-measurement">
                        <p>
                            <strong>{measurement.status === MeasurementStatus.MEASURED ? 'Measured' : measurement.status === MeasurementStatus.ASSUMED ? 'Assumed' : 'Not measured'}</strong>
                            {measurement.reason ? `: ${measurement.reason}` : ''}
                        </p>
                        <p>
                            Gain {formatNumber(measurement.gainEPerAdu, 3)} e⁻/ADU, read noise {formatNumber(measurement.readNoiseE, 2)} e⁻
                        </p>
                        {measurement.status === MeasurementStatus.MEASURED && !measurement.saved && !isConfirmingSave && (
                            <button type="button" className="btn quality-button" onClick={() => setIsConfirmingSave(true)}>
                                Save measurement
                            </button>
                        )}
                        {isConfirmingSave && (
                            <div className="equipment-block__confirm" role="group" aria-label="Confirm save">
                                <span>Save this measurement to the calibration library?</span>
                                <button type="button" className="btn btn--primary quality-button" onClick={() => void save()}>
                                    Yes, save
                                </button>
                                <button type="button" className="btn quality-button" onClick={() => setIsConfirmingSave(false)}>
                                    Cancel
                                </button>
                            </div>
                        )}
                        {saveResult && <p role="status">{saveResult}</p>}
                    </div>
                )}
            </div>
        </SectionPanel>
    );
};
