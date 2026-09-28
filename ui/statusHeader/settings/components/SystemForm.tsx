import { ConfigData } from '../utils/configUtils';
import { DISPLAY_DEFINITIONS, isDisplayEnabled } from '../../../common/constants/displayFlags';

interface SystemFormProps {
    backendInput: string;
    setBackendInput: (v: string) => void;
    validationError: string | null;
    secondaryWindowEnabled: boolean;
    handleToggleSecondaryWindow: (enabled: boolean) => void;
    configData: ConfigData;
    handleConfigChange: (section: string, key: string, value: string) => void;
    agentShortcut: string;
    setAgentShortcutInput: (v: string) => void;
    loadingConfig?: boolean;
}

export const SystemForm: React.FC<SystemFormProps> = ({
    backendInput,
    setBackendInput,
    validationError,
    secondaryWindowEnabled,
    handleToggleSecondaryWindow,
    configData,
    handleConfigChange,
    agentShortcut,
    setAgentShortcutInput,
    loadingConfig = false
}) => {
    const allowCommands = configData['Observatory.Telescope']?.['allow_commands'] === 'true' ||
        configData['Telescope']?.['allow_commands'] === 'true';

    return (
        <div className="settings__form">
            <h3>Frontend Settings</h3>
            <label className="settings__field">
                <span className="settings__label">Backend URL / IP</span>
                <input
                    className="settings__input"
                    type="text"
                    value={backendInput}
                    onChange={(e) => setBackendInput(e.target.value)}
                    placeholder="http://127.0.0.1:5000"
                    aria-label="Backend URL or IP"
                    aria-invalid={validationError ? 'true' : 'false'}
                />
            </label>
            {validationError && (
                <div className="settings__error" role="alert">
                    {validationError}
                </div>
            )}

            <label className="settings__field">
                <span className="settings__label">Command Palette Shortcut</span>
                <input
                    className="settings__input"
                    type="text"
                    value={agentShortcut}
                    onChange={(e) => setAgentShortcutInput(e.target.value)}
                    placeholder="Ctrl+Space"
                    aria-label="Command Palette Shortcut"
                />
                <div className="settings__help">
                    Global shortcut to toggle the AI Command Palette.
                </div>
            </label>

            <div className="settings__divider">
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
            </div>

            <div className="settings__divider">
                <h4>Displays</h4>
                {loadingConfig && (
                    <div style={{ fontSize: '12px', opacity: 0.7, marginBottom: '8px' }}>
                        Loading display preferences...
                    </div>
                )}
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
                <h3>Hardware Control</h3>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={allowCommands}
                        onChange={(e) => {
                            const section = configData['Observatory.Telescope'] ? 'Observatory.Telescope' : 'Telescope';
                            handleConfigChange(section, 'allow_commands', e.target.checked ? 'true' : 'false');
                        }}
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
