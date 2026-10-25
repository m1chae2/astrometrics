/**
 * @file CameraProfilesPanel.tsx
 * @description The camera profiles from the settings, the measured gain and
 * read noise of each camera setting, and the measurement call. The noise
 * table is compact: the camera name with its setting on a second line, and each gain and read noise
 * value followed by a chip with the evidence word the library gives it
 * (Measured, Partly measured or Tentative). Cameras with no entry are listed
 * in one short line. The
 * measurement form opens from a "Measure noise" button in the panel header
 * and closes again; focus moves into the form on open and back to the button
 * on close. A measurement is shown first; saving it to the calibration
 * library is a local write, so the panel asks before it saves.
 */

import React, { useEffect, useRef, useState } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { StatusChip, type StatusChipStatus } from '../../common/components/StatusChip';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import { callBackend } from '../../common/services/backendApi';
import type { CameraProfile } from '../../common/hooks/useEquipmentConfiguration';
import {
    MeasurementStatus,
    NoiseEvidenceStatus,
    type CameraNoiseSetting,
    type CameraNoiseValue,
    type SensorNoiseMeasurement,
} from '../../common/types/backendTypes';
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

/** The words for each evidence status, as the validation register spells them. */
const EVIDENCE_WORDS: Record<NoiseEvidenceStatus, string> = {
    [NoiseEvidenceStatus.MEASURED]: 'Measured',
    [NoiseEvidenceStatus.PARTLY_MEASURED]: 'Partly measured',
    [NoiseEvidenceStatus.DESIGN_ESTIMATE]: 'Tentative',
};

/** The chip look for each evidence status. */
const EVIDENCE_CHIP_STATUS: Record<NoiseEvidenceStatus, StatusChipStatus> = {
    [NoiseEvidenceStatus.MEASURED]: 'ok',
    [NoiseEvidenceStatus.PARTLY_MEASURED]: 'info',
    [NoiseEvidenceStatus.DESIGN_ESTIMATE]: 'warning',
};

/**
 * Shows one gain or read noise followed by a chip with its evidence word, or a dash when none is recorded.
 * @param recorded The recorded number, or nothing.
 * @param digits Decimal places.
 * @return The cell content.
 */
function noiseCell(recorded: CameraNoiseValue | null | undefined, digits: number): React.ReactNode {
    if (!recorded) return '—';
    return (
        <span className="camera-profiles__value">
            {recorded.value.toFixed(digits)}
            <StatusChip status={EVIDENCE_CHIP_STATUS[recorded.status]} label={EVIDENCE_WORDS[recorded.status]} />
        </span>
    );
}

/**
 * Formats one camera setting as a single line, for example "Gain 0 · Offset 10 · 1x1 · -10.0 °C".
 * @param setting The recorded setting.
 * @return The text.
 */
function settingText(setting: CameraNoiseSetting): string {
    const parts = [`Gain ${setting.gainSetting}`, `Offset ${setting.offset}`, String(setting.binning)];
    if (typeof setting.temperatureC === 'number') parts.push(`${setting.temperatureC.toFixed(0)} °C`.replace('-', '−'));
    return parts.join(' · ');
}

/**
 * Shows camera profiles, the recorded noise values and the noise measurement.
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

    const [isFormOpen, setIsFormOpen] = useState(false);
    const openButtonRef = useRef<HTMLButtonElement>(null);
    const cameraSelectRef = useRef<HTMLSelectElement>(null);
    const saveButtonRef = useRef<HTMLButtonElement>(null);
    const measurementRef = useRef<HTMLDivElement>(null);
    // Where focus goes after the confirm step closes: back to the save button, or to the result.
    const [focusAfterConfirm, setFocusAfterConfirm] = useState<'saveButton' | 'result' | null>(null);

    // Focus moves into the form once it is drawn.
    useEffect(() => {
        if (isFormOpen) cameraSelectRef.current?.focus();
    }, [isFormOpen]);

    useEffect(() => {
        if (focusAfterConfirm === 'saveButton') saveButtonRef.current?.focus();
        else if (focusAfterConfirm === 'result') measurementRef.current?.focus();
        if (focusAfterConfirm) setFocusAfterConfirm(null);
    }, [focusAfterConfirm]);

    /** Opens the measurement form and moves focus into it. */
    const openForm = () => {
        setIsFormOpen(true);
    };

    /** Closes the measurement form and puts focus back on the button that opened it. */
    const closeForm = () => {
        setIsFormOpen(false);
        openButtonRef.current?.focus();
    };

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
            setFocusAfterConfirm('result');
        }
    };

    const noiseRows = cameras.flatMap((camera) => (camera.sensorNoise ?? []).map((setting: CameraNoiseSetting) => ({ camera, setting })));
    const camerasWithoutNoise = cameras.filter((camera) => (camera.sensorNoise ?? []).length === 0);

    let profiles: React.ReactNode;
    if (loading) {
        profiles = <QualityPanelState state="loading" />;
    } else if (error) {
        profiles = <QualityPanelState state="error" subject="camera profiles" onRetry={onRetry} />;
    } else if (cameras.length === 0) {
        profiles = <QualityPanelState state="empty" emptyMessage="No camera profiles. Add a camera in the settings." />;
    } else {
        profiles = null;
    }

    const canShowForm = !loading && !error && cameras.length > 0;
    const measureButton = canShowForm ? (
        <button
            ref={openButtonRef}
            type="button"
            className="btn btn--tiny quality-button camera-profiles__measure-button"
            aria-expanded={isFormOpen}
            aria-controls="camera-measure-form"
            onClick={() => (isFormOpen ? closeForm() : openForm())}
        >
            Measure noise
        </button>
    ) : null;

    return (
        <SectionPanel title="Camera Profiles" headerContent={measureButton}>
            <div className="equipment-block">
                {profiles}

                {!loading && !error && cameras.length > 0 && (
                    <div data-testid="camera-noise">
                        {noiseRows.length > 0 && (
                            <table className="calibration-table camera-profiles__table" aria-label="Recorded camera noise">
                                <thead>
                                    <tr>
                                        <th>Camera and setting</th>
                                        <th>Gain (e⁻/ADU)</th>
                                        <th>Read noise (e⁻)</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {noiseRows.map(({ camera, setting }) => (
                                        <tr key={`${camera.name}-${setting.gainSetting}-${setting.offset}-${setting.binning}`}>
                                            <td>
                                                <span className="camera-profiles__camera">{camera.name}</span>
                                                <span className="camera-profiles__setting">{settingText(setting)}</span>
                                            </td>
                                            <td>{noiseCell(setting.gain, 3)}</td>
                                            <td>{noiseCell(setting.readNoise, 2)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                        {camerasWithoutNoise.length > 0 && (
                            <p className="camera-profiles__missing" data-testid="camera-noise-not-measured">
                                Not measured: {camerasWithoutNoise.map((camera) => camera.name).join(', ')}
                            </p>
                        )}
                    </div>
                )}

                {isFormOpen && canShowForm && (
                    <div id="camera-measure-form">
                <form
                    className="equipment-block__form"
                    onKeyDown={(event) => { if (event.key === 'Escape') closeForm(); }}
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (canMeasure) void measure();
                    }}
                >
                    <label>
                        Camera{' '}
                        <select ref={cameraSelectRef} className="dropdown" value={chosenCamera} onChange={(event) => setCameraId(event.target.value)}>
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
                        {isMeasuring ? 'Measuring…' : 'Measure'}
                    </button>
                    <button type="button" className="btn quality-button" onClick={closeForm}>Close</button>
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
                    <div data-testid="noise-measurement" ref={measurementRef} tabIndex={-1}>
                        <p>
                            <strong>{measurement.status === MeasurementStatus.MEASURED ? 'Measured' : measurement.status === MeasurementStatus.ASSUMED ? 'Assumed' : 'Not measured'}</strong>
                            {measurement.reason ? `: ${measurement.reason}` : ''}
                        </p>
                        <p>
                            Gain {formatNumber(measurement.gainEPerAdu, 3)} e⁻/ADU, read noise {formatNumber(measurement.readNoiseE, 2)} e⁻
                        </p>
                        {measurement.status === MeasurementStatus.MEASURED && !measurement.saved && !isConfirmingSave && (
                            <button ref={saveButtonRef} type="button" className="btn quality-button" onClick={() => setIsConfirmingSave(true)}>
                                Save measurement
                            </button>
                        )}
                        {isConfirmingSave && (
                            <div className="equipment-block__confirm" role="group" aria-label="Confirm save">
                                <span>Save this measurement to the calibration library?</span>
                                <button type="button" className="btn btn--primary quality-button" onClick={() => void save()}>
                                    Yes, save
                                </button>
                                <button type="button" className="btn quality-button" onClick={() => { setIsConfirmingSave(false); setFocusAfterConfirm('saveButton'); }}>
                                    Cancel
                                </button>
                            </div>
                        )}
                        {saveResult && <p>{saveResult}</p>}
                    </div>
                )}
                    </div>
                )}
            </div>
        </SectionPanel>
    );
};
