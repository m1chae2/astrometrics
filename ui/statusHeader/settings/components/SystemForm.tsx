import { ConfigData } from '../utils/configUtils';

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
    setAgentShortcutInput
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
            </div>

            <div className="settings__divider">
                <h4>Displays</h4>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={configData['Frontend']?.['enable_planetarium'] === 'true'}
                        onChange={(e) => handleConfigChange('Frontend', 'enable_planetarium', e.target.checked ? 'true' : 'false')}
                        className="settings__checkbox"
                    />
                    <span>Planetarium Display</span>
                </label>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={configData['Frontend']?.['enable_astronomy'] === 'true'}
                        onChange={(e) => handleConfigChange('Frontend', 'enable_astronomy', e.target.checked ? 'true' : 'false')}
                        className="settings__checkbox"
                    />
                    <span>Astronomy Manager</span>
                </label>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={configData['Frontend']?.['enable_observatory'] === 'true'}
                        onChange={(e) => handleConfigChange('Frontend', 'enable_observatory', e.target.checked ? 'true' : 'false')}
                        className="settings__checkbox"
                    />
                    <span>Observatory Manager</span>
                </label>
                <label className="settings__field settings__field--row">
                    <input
                        type="checkbox"
                        checked={configData['Frontend']?.['enable_observation'] === 'true'}
                        onChange={(e) => handleConfigChange('Frontend', 'enable_observation', e.target.checked ? 'true' : 'false')}
                        className="settings__checkbox"
                    />
                    <span>Observation Manager</span>
                </label>
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
