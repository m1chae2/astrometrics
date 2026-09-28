import { ConfigData } from '../utils/configUtils';

interface ObservatoryFormProps {
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
}

const TELESCOPE_SECTION = 'Observatory.Telescope';
const CONSTRAINTS_SECTION = 'Observatory.Constraints';
const FILTERS_SECTION = 'Observatory.Filters';

export const ObservatoryForm: React.FC<ObservatoryFormProps> = ({
    configData,
    handleConfigChange,
    loadingConfig = false,
}) => {
    // allow_commands historically lived under either section name depending
    // on how old a config file is; write to whichever one is actually present.
    const telescopeSection = configData['Observatory.Telescope'] ? 'Observatory.Telescope' : 'Telescope';
    const allowCommands = configData[telescopeSection]?.['allow_commands'] === 'true';

    return (
        <div className="settings__form">
            <h3>Observatory</h3>

            <h4>Telescope Connection</h4>
            <label className="settings__field">
                <span className="settings__label">Mount Hostname</span>
                <input
                    className="settings__input"
                    type="text"
                    value={String(configData[TELESCOPE_SECTION]?.['hostname'] ?? '')}
                    onChange={(e) => handleConfigChange(TELESCOPE_SECTION, 'hostname', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>
            <label className="settings__field">
                <span className="settings__label">INDI Port</span>
                <input
                    className="settings__input"
                    type="text"
                    value={String(configData[TELESCOPE_SECTION]?.['indi_port'] ?? '')}
                    onChange={(e) => handleConfigChange(TELESCOPE_SECTION, 'indi_port', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>
            <label className="settings__field">
                <span className="settings__label">Remote Pictures Path</span>
                <input
                    className="settings__input"
                    type="text"
                    value={String(configData[TELESCOPE_SECTION]?.['remote_pictures_path'] ?? '')}
                    onChange={(e) => handleConfigChange(TELESCOPE_SECTION, 'remote_pictures_path', e.target.value)}
                    disabled={loadingConfig}
                />
            </label>

            <div className="settings__divider">
                <h4>Altitude Constraints</h4>
                <label className="settings__field">
                    <span className="settings__label">Minimum Altitude (deg)</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[CONSTRAINTS_SECTION]?.['min_altitude'] ?? '')}
                        onChange={(e) => handleConfigChange(CONSTRAINTS_SECTION, 'min_altitude', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
                <label className="settings__field">
                    <span className="settings__label">Maximum Altitude (deg)</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[CONSTRAINTS_SECTION]?.['max_altitude'] ?? '')}
                        onChange={(e) => handleConfigChange(CONSTRAINTS_SECTION, 'max_altitude', e.target.value)}
                        disabled={loadingConfig}
                    />
                </label>
            </div>

            <div className="settings__divider">
                <h4>Filters</h4>
                <label className="settings__field">
                    <span className="settings__label">Available Filters</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[FILTERS_SECTION]?.['available'] ?? '')}
                        onChange={(e) => handleConfigChange(FILTERS_SECTION, 'available', e.target.value)}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        Comma-separated filter names, matching what the filter wheel is actually loaded with.
                    </div>
                </label>
            </div>

            <div className="settings__divider">
                <h4>Safety</h4>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={allowCommands}
                        onChange={(e) => handleConfigChange(telescopeSection, 'allow_commands', e.target.checked ? 'true' : 'false')}
                        className="settings__checkbox"
                        disabled={loadingConfig}
                    />
                    <span>Allow Telescope Commands (Disable Safe Mode)</span>
                </label>
                <div className="settings__help">
                    Enable this to allow the LLM and UI to move your telescope.
                </div>
            </div>
        </div>
    );
};
