import { ConfigData } from '../utils/configUtils';
import { SettingsCollectionEditor, parseList, CollectionFieldSchema } from './SettingsCollectionEditor';
import { parseCalibrationValue } from '../utils/parseCalibrationValue';

interface EquipmentFormProps {
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
}

const OPTIC_FIELDS: CollectionFieldSchema[] = [
    { key: 'focal_length_mm', label: 'Focal Length (mm)' },
    { key: 'focal_ratio', label: 'Focal Ratio' },
];

const SETUP_FIELDS: CollectionFieldSchema[] = [
    {
        key: 'camera',
        label: 'Camera',
        type: 'select',
        options: (configData) => parseList(configData['Observatory.Camera']?.['models']),
    },
    {
        key: 'optic',
        label: 'Optic',
        type: 'select',
        options: (configData) => parseList(configData['Observatory.Optics']?.['available']),
    },
];

const CAMERA_FIELDS: CollectionFieldSchema[] = [
    { key: 'name', label: 'Name', seedFromName: true },
    { key: 'name_aliases', label: 'Name Aliases' },
    { key: 'record_name', label: 'Record Name' },
    { key: 'pixel_size_μm', label: 'Pixel Size (μm)' },
    { key: 'sensor_width_px', label: 'Sensor Width (px)' },
    { key: 'sensor_height_px', label: 'Sensor Height (px)' },
    { key: 'grating_distance_mm', label: 'Grating Distance (mm)' },
    { key: 'sensor_min_wavelength', label: 'Sensor Min Wavelength (nm)' },
    { key: 'sensor_max_wavelength', label: 'Sensor Max Wavelength (nm)' },
    { key: 'default_iso', label: 'Default ISO' },
];

const CALIBRATION_FIELDS: Array<{ key: string; label: string }> = [
    { key: 'clip_ceiling_adu', label: 'Clip Ceiling (ADU)' },
    { key: 'saturation_threshold_adu', label: 'Saturation Threshold (ADU)' },
    { key: 'photometric_linearity_limit_adu', label: 'Photometric Linearity Limit (ADU)' },
];

export const EquipmentForm: React.FC<EquipmentFormProps> = ({
    configData,
    handleConfigChange,
    loadingConfig = false,
}) => {
    return (
        <div className="settings__form">
            <h3>Equipment</h3>
            <div className="settings__help" style={{ marginLeft: 0, marginBottom: '8px' }}>
                Add and edit the optics, camera/optic pairings, and cameras your
                observatory uses. Calibration-derived camera fields are shown
                read-only — they come from running calibration, not hand-editing.
            </div>

            <h4>Optics</h4>
            <SettingsCollectionEditor
                availableSection="Observatory.Optics"
                availableKey="available"
                itemSectionPrefix="Observatory.Optic."
                fields={OPTIC_FIELDS}
                configData={configData}
                handleConfigChange={handleConfigChange}
                loadingConfig={loadingConfig}
                addPlaceholder="New optic name (e.g. Apertura 75Q)"
            />

            <div className="settings__divider">
                <h4>Setups</h4>
                <SettingsCollectionEditor
                    availableSection="Observatory.Setups"
                    availableKey="available"
                    itemSectionPrefix="Observatory.Setup."
                    fields={SETUP_FIELDS}
                    configData={configData}
                    handleConfigChange={handleConfigChange}
                    loadingConfig={loadingConfig}
                    addPlaceholder="New setup name (e.g. D5300 on Apertura)"
                />
            </div>

            <div className="settings__divider">
                <h4>Cameras</h4>
                <SettingsCollectionEditor
                    availableSection="Observatory.Camera"
                    availableKey="models"
                    itemSectionPrefix="Observatory.Camera."
                    fields={CAMERA_FIELDS}
                    configData={configData}
                    handleConfigChange={handleConfigChange}
                    loadingConfig={loadingConfig}
                    addPlaceholder="New camera name"
                    renderExtra={(_name, section) => {
                        const rows = CALIBRATION_FIELDS
                            .map(({ key, label }) => ({ label, parsed: parseCalibrationValue(configData[section]?.[key]) }))
                            .filter((row) => row.parsed);
                        if (rows.length === 0) return null;
                        return (
                            <div className="settings__collection-row__calibration">
                                <div className="settings__label">Calibration Data (read-only, set by running calibration)</div>
                                {rows.map(({ label, parsed }) => (
                                    <div key={label} className="settings__collection-row__calibration-item">
                                        <span>{label}: {String(parsed!.value)}</span>
                                        <span className="settings__help" style={{ marginLeft: 0 }}>
                                            {parsed!.kind}{parsed!.source ? ` — ${parsed!.source}` : ''}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        );
                    }}
                />
            </div>
        </div>
    );
};
