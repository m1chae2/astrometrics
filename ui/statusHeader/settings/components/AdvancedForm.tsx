import { ConfigData } from '../utils/configUtils';

interface AdvancedFormProps {
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
}

const LOCAL_SOLVER_SECTION = 'Processing.Astrometry.Local Solver';
const SIRIL_SECTION = 'Processing.Siril';
const PARALLELISM_SECTION = 'Processing.Parallelism';

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
                <input
                    className="settings__input"
                    type="text"
                    value={String(configData[LOCAL_SOLVER_SECTION]?.['index_path'] ?? '')}
                    onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'index_path', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>
            <label className="settings__field settings__field--row">
                <input
                    type="checkbox"
                    checked={configData[LOCAL_SOLVER_SECTION]?.['autoindex'] === 'true'}
                    onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'autoindex', e.target.checked ? 'true' : 'false')}
                    className="settings__checkbox"
                    disabled={loadingConfig}
                />
                <span>Autoindex</span>
            </label>
            <label className="settings__field">
                <span className="settings__label">CPU Limit (seconds)</span>
                <input
                    className="settings__input"
                    type="text"
                    value={String(configData[LOCAL_SOLVER_SECTION]?.['cpulimit'] ?? '')}
                    onChange={(e) => handleConfigChange(LOCAL_SOLVER_SECTION, 'cpulimit', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>

            <div className="settings__divider">
                <h4>Siril</h4>
                <label className="settings__field">
                    <span className="settings__label">Stack Weight</span>
                    <input
                        className="settings__input"
                        type="text"
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
                <h4>Parallelism</h4>
                <label className="settings__field">
                    <span className="settings__label">Target Workers</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[PARALLELISM_SECTION]?.['target_workers'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'target_workers', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Photometry Workers</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[PARALLELISM_SECTION]?.['photometry_workers'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'photometry_workers', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Worker Niceness</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[PARALLELISM_SECTION]?.['worker_niceness'] ?? '')}
                        onChange={(e) => handleConfigChange(PARALLELISM_SECTION, 'worker_niceness', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Max Concurrent Jobs</span>
                    <input
                        className="settings__input"
                        type="text"
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
