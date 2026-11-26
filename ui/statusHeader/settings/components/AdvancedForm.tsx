import { Checkbox } from '../../../common/components/Checkbox';
import { PopoverSelect } from '../../../common/components/PopoverSelect';
import { TextInput } from '../../../common/components/TextInput';
import { ConfigData } from '../utils/configUtils';

interface AdvancedFormProps {
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
}

const LOCAL_SOLVER_SECTION = 'Processing.Astrometry.Local Solver';
const SIRIL_SECTION = 'Processing.Siril';
const PARALLELISM_SECTION = 'Processing.Parallelism';
const CATALOGS_SECTION = 'Catalogs';
const SIMBAD_SERVER_OPTIONS = [
    { id: 'simbad.harvard.edu', label: 'Harvard (simbad.harvard.edu)' },
    { id: 'simbad.cds.unistra.fr', label: 'Strasbourg (simbad.cds.unistra.fr)' },
];
const VIZIER_SERVER_OPTIONS = [
    { id: 'vizier.cfa.harvard.edu', label: 'Harvard (vizier.cfa.harvard.edu)' },
    { id: 'vizier.cds.unistra.fr', label: 'Strasbourg (vizier.cds.unistra.fr)' },
];

export const AdvancedForm: React.FC<AdvancedFormProps> = ({
    configData,
    handleConfigChange,
    loadingConfig = false,
}) => {
    return (
        <div className="settings__form">
            <h3>Advanced</h3>
            <div className="settings__help" style={{ marginLeft: 0, marginBottom: '8px' }}>
                Install-time and performance settings. Most users won't need to
                touch these after initial setup.
            </div>

            <h4>Local Astrometry Solver</h4>
            <label className="settings__field">
                <span className="settings__label">Index Path</span>
                <TextInput
                    value={String(configData[LOCAL_SOLVER_SECTION]?.['index_path'] ?? '')}
                    onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'index_path', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>
            <Checkbox
                className="settings__field settings__field--row"
                label="Autoindex"
                checked={configData[LOCAL_SOLVER_SECTION]?.['autoindex'] === 'true'} onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'autoindex', e.target.checked ? 'true' : 'false')} disabled={loadingConfig}
            />
            <label className="settings__field">
                <span className="settings__label">CPU Limit (seconds)</span>
                <TextInput
                    value={String(configData[LOCAL_SOLVER_SECTION]?.['cpulimit'] ?? '')}
                    onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'cpulimit', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>

            <div className="settings__divider">
                <h4>Siril</h4>
                <label className="settings__field">
                    <span className="settings__label">Stack Weight</span>
                    <TextInput
                        value={String(configData[SIRIL_SECTION]?.['stack_weight'] ?? '')}
                        onChange={(e) => handleConfigChange(SIRIL_SECTION, 'stack_weight', e.target.value)}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        Leave blank unless installed via Flatpak. Set to "wfwhm" to
                        weight frames by measured star sharpness.
                    </div>
                </label>
            </div>

            <div className="settings__divider">
                <h4>Catalogs</h4>
                <div className="settings__field settings__field--spaced">
                    <span className="settings__label">SIMBAD Server</span>
                    <PopoverSelect
                        label="SIMBAD Server"
                        options={SIMBAD_SERVER_OPTIONS}
                        selectedId={String(configData[CATALOGS_SECTION]?.['simbad_preferred_server'] || 'simbad.harvard.edu')}
                        onSelect={(id) => handleConfigChange(CATALOGS_SECTION, 'simbad_preferred_server', id ?? 'simbad.harvard.edu')}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        The SIMBAD server asked first. If it cannot be reached, the other one is used.
                    </div>
                </div>
                <div className="settings__field settings__field--spaced">
                    <span className="settings__label">VizieR Server</span>
                    <PopoverSelect
                        label="VizieR Server"
                        options={VIZIER_SERVER_OPTIONS}
                        selectedId={String(configData[CATALOGS_SECTION]?.['vizier_preferred_server'] || 'vizier.cfa.harvard.edu')}
                        onSelect={(id) => handleConfigChange(CATALOGS_SECTION, 'vizier_preferred_server', id ?? 'vizier.cfa.harvard.edu')}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        The VizieR server asked first, for the Gaia star tiles. If it cannot be reached, the other one is used.
                    </div>
                </div>
            </div>

            <div className="settings__divider">
                <h4>Parallelism</h4>
                <label className="settings__field">
                    <span className="settings__label">Target Workers</span>
                    <TextInput
                        value={String(configData[PARALLELISM_SECTION]?.['target_workers'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'target_workers', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Photometry Workers</span>
                    <TextInput
                        value={String(configData[PARALLELISM_SECTION]?.['photometry_workers'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'photometry_workers', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Worker Niceness</span>
                    <TextInput
                        value={String(configData[PARALLELISM_SECTION]?.['worker_niceness'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'worker_niceness', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Max Concurrent Jobs</span>
                    <TextInput
                        value={String(configData[PARALLELISM_SECTION]?.['max_concurrent_jobs'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'max_concurrent_jobs', e.target.value)}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        Max concurrent heavy jobs, shared by stacking and photometry/spectroscopy analysis.
                    </div>
                </label>
            </div>
        </div>
    );
};
