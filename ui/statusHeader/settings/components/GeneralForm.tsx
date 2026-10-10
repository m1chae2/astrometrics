import { ConfigData } from '../utils/configUtils';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from '../../../common/constants/displayFlags';

interface GeneralFormProps {
    secondaryWindowEnabled: boolean;
    handleToggleSecondaryWindow: (enabled: boolean) => void;
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    loadingConfig?: boolean;
    isReindexing: boolean;
    reindexingStatus: string | null;
    reindexingProgress: number;
    onReindex: () => void;
}

const IMAGE_LIBRARY_SECTION = 'Image Library';

export const GeneralForm: React.FC<GeneralFormProps> = ({
    secondaryWindowEnabled,
    handleToggleSecondaryWindow,
    configData,
    handleConfigChange,
    loadingConfig = false,
    isReindexing,
    reindexingStatus,
    reindexingProgress,
    onReindex,
}) => {
    return (
        <div className="settings__form">
            <h3>General</h3>

            <h4>Secondary Window</h4>
            <label className="settings__field settings__field--row">
                <input
                    type="checkbox"
                    checked={secondaryWindowEnabled}
                    onChange={(e) => handleToggleSecondaryWindow(e.target.checked)}
                    className="settings__checkbox"
                />
                <span>Enable Secondary Window</span>
            </label>
            {secondaryWindowEnabled && (
                <label className="settings__field" style={{ marginTop: '8px' }}>
                    <span className="settings__label">Secondary Window Display</span>
                    <select
                        className="settings__input"
                        value={String(configData['Frontend']?.['secondary_window_mode'] || 'Image Processing')}
                        onChange={(e) => handleConfigChange('Frontend', 'secondary_window_mode', e.target.value)}
                        aria-label="Secondary Window Default Display"
                    >
                        {DISPLAY_DEFINITIONS.filter(({ mode }) => isDisplayEnabled(configData, mode)).map(({ mode }) => (
                            <option key={mode} value={mode}>{mode}</option>
                        ))}
                    </select>
                </label>
            )}

            <div className="settings__divider">
                <h4>Displays</h4>
                {DISPLAY_DEFINITIONS.map(({ mode, flag }) => (
                    <label className="settings__field settings__field--row" key={flag}>
                        <input
                            type="checkbox"
                            checked={configData['Frontend']?.[flag] !== 'false'}
                            onChange={(e) => handleConfigChange('Frontend', flag, e.target.checked ? 'true' : 'false')}
                            className="settings__checkbox"
                            disabled={loadingConfig}
                        />
                        <span>{mode}</span>
                    </label>
                ))}
            </div>

            <div className="settings__divider">
                <h4>Image Library</h4>
                <label className="settings__field">
                    <span className="settings__label">Library Path</span>
                    <input
                        className="settings__input"
                        type="text"
                        value={String(configData[IMAGE_LIBRARY_SECTION]?.['path'] ?? '')}
                        onChange={(e) => handleConfigChange(IMAGE_LIBRARY_SECTION, 'path', e.target.value)}
                        disabled={loadingConfig}
                    />
                    <div className="settings__help">
                        Absolute path to your image library. Captured frames live in a
                        "frames" subfolder underneath this path.
                    </div>
                </label>

                <div className="settings__action-section">
                    <h4 className="settings__subgroup-title">Maintenance</h4>
                    <div className="settings__action-row">
                        <button
                            className={`btn ${isReindexing ? 'btn--disabled' : ''} settings__action-label`}
                            onClick={onReindex}
                            disabled={isReindexing}
                            type="button"
                        >
                            {isReindexing ? 'Reindexing...' : 'Reindex Library'}
                        </button>
                        {isReindexing && (
                            <div className="settings__progress-info">
                                <span className="settings__progress-text">{reindexingStatus}</span>
                                <div className="settings__progress-bar">
                                    <div
                                        className={`settings__progress-fill ${!reindexingProgress ? 'settings__progress-fill--animate' : ''}`}
                                        style={{ width: `${reindexingProgress || 0}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                    <p className="settings__help-text">
                        Scans all local folders (lights, darks, flats, biases) and synchronizes the index files.
                    </p>
                </div>
            </div>
        </div>
    );
};
